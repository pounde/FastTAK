package main

import (
	"encoding/base64"
	"fmt"
	"log"
	"net/http"
	"strings"

	ldap "github.com/go-ldap/ldap/v3"
)

type AuthHandler struct {
	proxy *LDAPProxy
}

func NewAuthHandler(proxy *LDAPProxy) *AuthHandler {
	return &AuthHandler{proxy: proxy}
}

// HandleVerify implements GET /auth/verify for Caddy forward_auth.
// Reads Basic auth header, does LDAP bind, returns 200 or 401.
func (a *AuthHandler) HandleVerify(w http.ResponseWriter, r *http.Request) {
	authHeader := r.Header.Get("Authorization")
	if authHeader == "" || !strings.HasPrefix(authHeader, "Basic ") {
		w.Header().Set("WWW-Authenticate", `Basic realm="FastTAK"`)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	decoded, err := base64.StdEncoding.DecodeString(authHeader[6:])
	if err != nil {
		http.Error(w, "Invalid auth header", http.StatusBadRequest)
		return
	}

	parts := strings.SplitN(string(decoded), ":", 2)
	if len(parts) != 2 {
		http.Error(w, "Invalid auth header", http.StatusBadRequest)
		return
	}

	username, password := parts[0], parts[1]
	bindDN := fmt.Sprintf("uid=%s,ou=people,%s", username, a.proxy.baseDN)

	ok, err := a.proxy.HandleBind(bindDN, password)
	if err != nil {
		log.Printf("auth verify error for %s: %v", username, err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	if !ok {
		w.Header().Set("WWW-Authenticate", `Basic realm="FastTAK"`)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	// Fetch user groups from LLDAP for the Remote-Groups header
	groups, err := a.fetchUserGroups(username)
	if err != nil {
		log.Printf("failed to fetch groups for %s: %v", username, err)
	}

	w.Header().Set("Remote-User", username)
	w.Header().Set("Remote-Groups", strings.Join(groups, ","))
	w.WriteHeader(http.StatusOK)
}

// fetchUserGroups queries LLDAP for the user's group memberships.
func (a *AuthHandler) fetchUserGroups(username string) ([]string, error) {
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", a.proxy.lldapAddr))
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	// Bind as admin to search
	if err := conn.Bind(adminBindDN, adminBindPass); err != nil {
		return nil, fmt.Errorf("admin bind: %w", err)
	}

	searchReq := ldap.NewSearchRequest(
		fmt.Sprintf("ou=groups,%s", a.proxy.baseDN),
		ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 0, 0, false,
		fmt.Sprintf("(member=uid=%s,ou=people,%s)", ldap.EscapeFilter(username), a.proxy.baseDN),
		[]string{"cn"},
		nil,
	)

	sr, err := conn.Search(searchReq)
	if err != nil {
		return nil, err
	}

	var groups []string
	for _, entry := range sr.Entries {
		groups = append(groups, entry.GetAttributeValue("cn"))
	}
	return groups, nil
}

// Package-level admin credentials, set at startup from env vars.
var adminBindDN string
var adminBindPass string
