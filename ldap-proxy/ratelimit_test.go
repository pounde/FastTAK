package main

import (
	"testing"
	"time"
)

// fakeClock returns a clock function backed by a *time.Time pointer so tests
// can advance time deterministically without sleeping.
func fakeClock(t *time.Time) func() time.Time {
	return func() time.Time { return *t }
}

func advance(t *time.Time, d time.Duration) {
	*t = t.Add(d)
}

func TestRateLimiter_UnderLimit_Allows(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	for i := 0; i < 2; i++ {
		allowed, retryAfter := rl.Check("10.0.0.1")
		if !allowed {
			t.Fatalf("attempt %d (under limit): expected allowed, got blocked", i+1)
		}
		if retryAfter != 0 {
			t.Fatalf("attempt %d: expected retryAfter=0, got %v", i+1, retryAfter)
		}
	}
}

func TestRateLimiter_AtLimit_Blocks(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Burn through the budget.
	for i := 0; i < 3; i++ {
		rl.Check("10.0.0.1")
	}

	// The Nth attempt (4th, already over limit) should be blocked.
	allowed, retryAfter := rl.Check("10.0.0.1")
	if allowed {
		t.Fatal("at/over limit: expected blocked, got allowed")
	}
	if retryAfter <= 0 {
		t.Fatalf("expected positive retryAfter, got %v", retryAfter)
	}
}

func TestRateLimiter_OverLimit_LockoutApplies(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	lockout := 5 * time.Minute
	rl := NewRateLimiter(1*time.Minute, lockout, 3, fakeClock(&now))

	// Exhaust budget — the 3rd attempt triggers lockout.
	for i := 0; i < 3; i++ {
		rl.Check("10.0.0.2")
	}

	// Advance time but stay inside the lockout window.
	advance(&now, 2*time.Minute)
	allowed, retryAfter := rl.Check("10.0.0.2")
	if allowed {
		t.Fatal("inside lockout: expected blocked")
	}
	// retryAfter should reflect remaining lockout (~3 min), not the full 5 min.
	if retryAfter <= 0 || retryAfter > lockout {
		t.Fatalf("unexpected retryAfter inside lockout: %v", retryAfter)
	}
}

func TestRateLimiter_WindowRollover_Unblocks(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	window := 1 * time.Minute
	rl := NewRateLimiter(window, 5*time.Minute, 3, fakeClock(&now))

	// Make 2 attempts — one short of the limit.
	rl.Check("10.0.0.3")
	rl.Check("10.0.0.3")

	// Roll the window forward so those attempts age out.
	advance(&now, window+1*time.Second)

	// Should be allowed again (slate wiped by window expiry).
	allowed, _ := rl.Check("10.0.0.3")
	if !allowed {
		t.Fatal("after window rollover: expected allowed, got blocked")
	}
}

func TestRateLimiter_LockoutExpires_Unblocks(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	lockout := 5 * time.Minute
	rl := NewRateLimiter(1*time.Minute, lockout, 3, fakeClock(&now))

	// Exhaust budget to trigger lockout.
	for i := 0; i < 3; i++ {
		rl.Check("10.0.0.4")
	}

	// Advance past the lockout.
	advance(&now, lockout+1*time.Second)

	allowed, retryAfter := rl.Check("10.0.0.4")
	if !allowed {
		t.Fatal("after lockout expiry: expected allowed, got blocked")
	}
	if retryAfter != 0 {
		t.Fatalf("after lockout expiry: expected retryAfter=0, got %v", retryAfter)
	}
}

func TestRateLimiter_PerIPIsolation(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Exhaust budget for IP A.
	for i := 0; i < 3; i++ {
		rl.Check("10.0.0.5")
	}

	// IP B should be completely unaffected.
	allowed, retryAfter := rl.Check("10.0.0.6")
	if !allowed {
		t.Fatal("per-IP isolation: IP B should be allowed after IP A is blocked")
	}
	if retryAfter != 0 {
		t.Fatalf("per-IP isolation: IP B retryAfter should be 0, got %v", retryAfter)
	}
}
