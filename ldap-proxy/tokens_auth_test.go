package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRequireBearer(t *testing.T) {
	const secret = "s3cret-value"
	var reached bool
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	})
	h := requireBearer(secret, inner)

	cases := []struct {
		name       string
		header     string
		wantStatus int
		wantReach  bool
	}{
		{"no header", "", http.StatusUnauthorized, false},
		{"wrong secret", "Bearer nope", http.StatusUnauthorized, false},
		{"missing bearer prefix", secret, http.StatusUnauthorized, false},
		{"correct secret", "Bearer " + secret, http.StatusOK, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			reached = false
			req := httptest.NewRequest("POST", "/tokens", nil)
			if tc.header != "" {
				req.Header.Set("Authorization", tc.header)
			}
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d", rec.Code, tc.wantStatus)
			}
			if reached != tc.wantReach {
				t.Errorf("inner reached = %v, want %v", reached, tc.wantReach)
			}
		})
	}
}
