package main

import (
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
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

	// Admin credentials for group lookups in /auth/verify
	adminBindDN = envOrDefault("LDAP_ADMIN_DN", "uid=adm_ldapservice,ou=people,"+baseDN)
	adminBindPass = os.Getenv("LDAP_BIND_PASSWORD")
	if adminBindPass == "" {
		log.Fatal("LDAP_BIND_PASSWORD is required but not set")
	}

	// Initialize token store
	tokens, err := NewTokenStore(dbPath)
	if err != nil {
		log.Fatalf("Failed to initialize token store: %v", err)
	}

	// Initialize proxy
	proxy := NewLDAPProxy(tokens, lldapAddr, baseDN)

	// REST API
	tokensAPI := NewTokensAPI(tokens, defaultTTL, defaultOneTime)
	authHandler := NewAuthHandler(proxy)
	healthHandler := NewHealthHandler(tokens, proxy)

	// HTTP mux
	mux := http.NewServeMux()
	mux.Handle("POST /tokens", tokensAPI)
	mux.Handle("GET /tokens/{username}", tokensAPI)
	mux.Handle("DELETE /tokens/{username}", tokensAPI)
	mux.HandleFunc("GET /auth/verify", authHandler.HandleVerify)
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
