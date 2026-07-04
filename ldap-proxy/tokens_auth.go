package main

import (
	"crypto/subtle"
	"net/http"
	"strings"
)

// requireBearer wraps h so that requests must carry `Authorization: Bearer
// <secret>`. The /tokens API mints an LDAP bind credential for any username, so
// leaving it unauthenticated lets any workload on the Docker network (e.g. the
// Node-RED scripting environment) forge credentials for admin accounts. The
// secret is shared with the monitor via the TOKENS_API_SECRET env var.
func requireBearer(secret string, h http.Handler) http.Handler {
	const prefix = "Bearer "
	want := []byte(secret)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authz := r.Header.Get("Authorization")
		if !strings.HasPrefix(authz, prefix) {
			http.Error(w, `{"error": "unauthorized"}`, http.StatusUnauthorized)
			return
		}
		got := []byte(strings.TrimPrefix(authz, prefix))
		// ConstantTimeCompare returns 0 on length mismatch without panicking.
		if subtle.ConstantTimeCompare(got, want) != 1 {
			http.Error(w, `{"error": "unauthorized"}`, http.StatusUnauthorized)
			return
		}
		h.ServeHTTP(w, r)
	})
}
