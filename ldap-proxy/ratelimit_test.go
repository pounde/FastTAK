package main

import (
	"net/http"
	"net/http/httptest"
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

// dummyHandler returns 200 OK and records how many times it was called.
type dummyHandler struct{ calls int }

func (h *dummyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.calls++
	w.WriteHeader(http.StatusOK)
}

// TestMiddleware_Under_PassesThrough — N-1 requests from the same IP all
// reach the inner handler with 200.
func TestMiddleware_Under_PassesThrough(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 5
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	inner := &dummyHandler{}
	handler := rl.Middleware(inner)

	for i := 0; i < maxAttempts-1; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "10.0.0.1:12345"
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d", i+1, rr.Code)
		}
	}
	if inner.calls != maxAttempts-1 {
		t.Fatalf("expected inner handler called %d times, got %d", maxAttempts-1, inner.calls)
	}
}

// TestMiddleware_AtLimit_Returns429 — after the budget-filling attempt, the
// next request returns 429 with a Retry-After header.
func TestMiddleware_AtLimit_Returns429(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 3
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	handler := rl.Middleware(&dummyHandler{})

	// Burn through the full budget (triggers lockout on last allowed request).
	for i := 0; i < maxAttempts; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "10.0.0.2:9999"
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	// The next request must be rejected.
	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "10.0.0.2:9999"
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429, got %d", rr.Code)
	}
	if rr.Header().Get("Retry-After") == "" {
		t.Fatal("expected Retry-After header, got none")
	}
}

// TestMiddleware_XForwardedFor_UsedForKeying — when X-Forwarded-For is
// present, the limiter keys on that IP, not on RemoteAddr.
func TestMiddleware_XForwardedFor_UsedForKeying(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 3
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	handler := rl.Middleware(&dummyHandler{})

	// Exhaust budget keyed on X-Forwarded-For "10.0.0.5".
	for i := 0; i < maxAttempts; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "127.0.0.1:1111"
		req.Header.Set("X-Forwarded-For", "10.0.0.5")
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	// Next request from the same X-Forwarded-For IP should be rate-limited.
	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "127.0.0.1:2222" // different port, same XFF
	req.Header.Set("X-Forwarded-For", "10.0.0.5")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 keyed on XFF IP, got %d", rr.Code)
	}
}

// TestMiddleware_MultipleIPsInXForwardedFor_UsesFirst — when X-Forwarded-For
// contains a comma-separated list, only the first entry is used.
func TestMiddleware_MultipleIPsInXForwardedFor_UsesFirst(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 3
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	handler := rl.Middleware(&dummyHandler{})

	// Exhaust budget using the first IP in a multi-value XFF header.
	for i := 0; i < maxAttempts; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "127.0.0.1:3333"
		req.Header.Set("X-Forwarded-For", "1.2.3.4, 5.6.7.8")
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	// Next request keyed on "1.2.3.4" should be blocked.
	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "127.0.0.1:4444"
	req.Header.Set("X-Forwarded-For", "1.2.3.4, 5.6.7.8")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 for first XFF IP, got %d", rr.Code)
	}

	// A request arriving only from 5.6.7.8 should NOT be blocked (different key).
	req2 := httptest.NewRequest("GET", "/auth/verify", nil)
	req2.RemoteAddr = "127.0.0.1:5555"
	req2.Header.Set("X-Forwarded-For", "5.6.7.8")
	rr2 := httptest.NewRecorder()
	handler.ServeHTTP(rr2, req2)

	if rr2.Code == http.StatusTooManyRequests {
		t.Fatal("5.6.7.8 should not be rate-limited — it was only a forwarding proxy")
	}
}

// TestMiddleware_NoXForwardedFor_FallsBackToRemoteAddr — without an XFF
// header, the limiter falls back to RemoteAddr with the port stripped.
func TestMiddleware_NoXForwardedFor_FallsBackToRemoteAddr(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 3
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	handler := rl.Middleware(&dummyHandler{})

	// Exhaust budget via RemoteAddr (no XFF).
	for i := 0; i < maxAttempts; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "192.168.1.1:6666"
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	// Same host, different port — should still be blocked (port is stripped).
	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "192.168.1.1:7777"
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 from RemoteAddr fallback, got %d", rr.Code)
	}
}

// TestMiddleware_MalformedXForwardedFor_FallsBackToRemoteAddr — a garbage XFF
// header value falls back to RemoteAddr.
func TestMiddleware_MalformedXForwardedFor_FallsBackToRemoteAddr(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	maxAttempts := 3
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, maxAttempts, fakeClock(&now))

	handler := rl.Middleware(&dummyHandler{})

	// Exhaust budget — malformed XFF should fall back to the RemoteAddr host.
	for i := 0; i < maxAttempts; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "172.16.0.1:8888"
		req.Header.Set("X-Forwarded-For", "!!!not-an-ip!!!")
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "172.16.0.1:9999"
	req.Header.Set("X-Forwarded-For", "!!!not-an-ip!!!")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 after malformed XFF falls back to RemoteAddr, got %d", rr.Code)
	}
}
