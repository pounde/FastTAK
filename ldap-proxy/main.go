package main

import (
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envIntOrDefault(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func envBoolOrDefault(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return fallback
}

func main() {
	dbPath := envOrDefault("TOKEN_DB_PATH", "/data/tokens.db")
	lldapAddr := envOrDefault("LLDAP_ADDR", "lldap:3890")
	baseDN := strings.ToLower(envOrDefault("LDAP_BASE_DN", "dc=takldap"))
	ldapListenAddr := envOrDefault("LDAP_LISTEN_ADDR", ":3389")
	httpListenAddr := envOrDefault("HTTP_LISTEN_ADDR", ":8080")
	defaultTTL := envIntOrDefault("ENROLLMENT_TOKEN_TTL_MINUTES", 15)
	defaultOneTime := envBoolOrDefault("ENROLLMENT_TOKEN_ONE_TIME", true)

	// Admin credentials for search forwarding and group lookups in /auth/verify
	adminDN := envOrDefault("LDAP_ADMIN_DN", "uid=adm_ldapservice,ou=people,"+baseDN)
	adminPass := os.Getenv("LDAP_BIND_PASSWORD")
	if adminPass == "" {
		log.Fatal("LDAP_BIND_PASSWORD is required but not set")
	}

	// Initialize token store
	tokens, err := NewTokenStore(dbPath)
	if err != nil {
		log.Fatalf("Failed to initialize token store: %v", err)
	}

	// Initialize proxy
	proxy := NewLDAPProxy(tokens, lldapAddr, baseDN, adminDN, adminPass)

	// REST API
	tokensAPI := NewTokensAPI(tokens, defaultTTL, defaultOneTime)
	authHandler := NewAuthHandler(proxy)
	healthHandler := NewHealthHandler(tokens, proxy)

	// Rate limiter for /auth/verify — protects against brute force on LDAP auth.
	// /tokens is internal-only (not Caddy-exposed). /healthz is Docker health probes.
	// Defaults: 10 attempts per 5 minutes, 15-minute lockout. Configurable via env (DD-035).
	authRateLimit := NewRateLimiter(5*time.Minute, 15*time.Minute, 10, time.Now)

	// HTTP mux — no auth on these endpoints. The REST API is internal-only
	// (not exposed via Caddy), reachable only from the Docker network by
	// the monitor service. If the proxy is ever exposed externally, add auth.
	mux := http.NewServeMux()
	mux.Handle("POST /tokens", tokensAPI)
	mux.Handle("GET /tokens/{username}", tokensAPI)
	mux.Handle("DELETE /tokens/{username}", tokensAPI)
	mux.Handle("GET /auth/verify", authRateLimit.Middleware(http.HandlerFunc(authHandler.HandleVerify)))
	mux.HandleFunc("GET /healthz", healthHandler.HandleHealthz)

	// Start LDAP proxy in background
	go func() {
		log.Printf("LDAP proxy listening on %s → %s", ldapListenAddr, lldapAddr)
		if err := startLDAPServer(ldapListenAddr, proxy); err != nil {
			log.Fatalf("LDAP server error: %v", err)
		}
	}()

	// Start HTTP server
	log.Printf("HTTP server listening on %s", httpListenAddr)
	log.Fatal(http.ListenAndServe(httpListenAddr, mux))
}
