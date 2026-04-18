package main

import (
	"sync"
	"time"
)

// ipState tracks per-IP attempt history and any active lockout.
type ipState struct {
	// attempts holds the timestamp of each recent attempt. Entries older than
	// the window are pruned on every Check call, keeping memory bounded.
	attempts []time.Time

	// lockoutUntil is zero when the IP is not locked out. Once maxAttempts is
	// reached, it is set to now+lockout and the IP is blocked until that time.
	lockoutUntil time.Time
}

// RateLimiter is a per-IP sliding-window rate limiter with lockout support.
//
// Design notes:
//   - Sliding window (vs. fixed bucket): avoids the burst-at-bucket-boundary
//     problem where an attacker can make 2×maxAttempts in a short span by
//     timing requests to straddle two fixed windows.
//   - In-memory state: ldap-proxy is a single-process, single-replica service.
//     There is no need for distributed state (Redis etc.) at this scale, and
//     keeping it in-process eliminates a runtime dependency.
//   - Lockout distinct from window: after exceeding the attempt budget, the IP
//     is locked out for a longer duration than the window, giving defenders
//     time to react and penalising automated scanners more severely.
type RateLimiter struct {
	window      time.Duration
	lockout     time.Duration
	maxAttempts int
	clock       func() time.Time

	mu    sync.Mutex
	state map[string]*ipState
}

// NewRateLimiter creates a limiter with the given window, lockout, max attempts,
// and clock function.
//
//   - window:      period over which attempts are counted (e.g. 1 minute).
//   - lockout:     how long an IP stays blocked after exceeding maxAttempts.
//   - maxAttempts: number of attempts permitted within a window before blocking.
//   - clock:       time source; pass time.Now for production, a fake for tests.
func NewRateLimiter(window, lockout time.Duration, maxAttempts int, clock func() time.Time) *RateLimiter {
	return &RateLimiter{
		window:      window,
		lockout:     lockout,
		maxAttempts: maxAttempts,
		clock:       clock,
		state:       make(map[string]*ipState),
	}
}

// Check records an attempt from ip and reports whether it is permitted.
//
// Returns:
//   - allowed=true, retryAfter=0 when the attempt is within the budget.
//   - allowed=false, retryAfter>0 when the IP is locked out; retryAfter is the
//     remaining lockout duration.
//
// Safe for concurrent use.
func (rl *RateLimiter) Check(ip string) (allowed bool, retryAfter time.Duration) {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := rl.clock()

	st, ok := rl.state[ip]
	if !ok {
		st = &ipState{}
		rl.state[ip] = st
	}

	// If the IP is currently locked out, report the remaining duration.
	if now.Before(st.lockoutUntil) {
		return false, st.lockoutUntil.Sub(now)
	}

	// Prune attempts that have aged out of the sliding window.
	cutoff := now.Add(-rl.window)
	fresh := st.attempts[:0]
	for _, ts := range st.attempts {
		if ts.After(cutoff) {
			fresh = append(fresh, ts)
		}
	}
	st.attempts = fresh

	// Record the attempt.
	st.attempts = append(st.attempts, now)

	// If the attempt count has reached the cap, apply lockout immediately.
	// We set lockout on the attempt that fills the last slot so that subsequent
	// checks (even after the window has rolled past the recorded attempts) still
	// see the lockout. The attempt itself is allowed — the *next* one is blocked.
	if len(st.attempts) >= rl.maxAttempts {
		st.lockoutUntil = now.Add(rl.lockout)
	}

	return true, 0
}
