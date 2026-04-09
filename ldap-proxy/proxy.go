package main

import (
	"fmt"
	"log"
	"strings"

	ldap "github.com/go-ldap/ldap/v3"
)

type LDAPProxy struct {
	tokens    *TokenStore
	lldapAddr string // e.g., "lldap:3890"
	baseDN    string // e.g., "dc=takldap"
}

func NewLDAPProxy(tokens *TokenStore, lldapAddr, baseDN string) *LDAPProxy {
	return &LDAPProxy{
		tokens:    tokens,
		lldapAddr: lldapAddr,
		baseDN:    baseDN,
	}
}

// extractUsername parses a bind DN like "uid=jsmith,ou=people,dc=takldap"
// and returns the uid value. Returns empty string if unparseable.
func extractUsername(bindDN, baseDN string) string {
	dn := strings.ToLower(bindDN)
	if !strings.HasSuffix(dn, ","+strings.ToLower(baseDN)) {
		return ""
	}
	parts := strings.Split(dn, ",")
	if len(parts) < 2 {
		return ""
	}
	cnPart := parts[0]
	if !strings.HasPrefix(cnPart, "uid=") {
		return ""
	}
	return strings.TrimPrefix(cnPart, "uid=")
}

// HandleBind processes an LDAP bind request.
// 1. Extract username from DN
// 2. Check token store
// 3. If no token match, forward to LLDAP
func (p *LDAPProxy) HandleBind(bindDN, password string) (bool, error) {
	username := extractUsername(bindDN, p.baseDN)
	if username != "" && password != "" {
		ok, err := p.tokens.Verify(username, password)
		if err != nil {
			log.Printf("token verify error for %s: %v", username, err)
		}
		if ok {
			log.Printf("token auth success for %s", username)
			return true, nil
		}
	}

	// Forward to LLDAP
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", p.lldapAddr))
	if err != nil {
		return false, fmt.Errorf("connect to lldap: %w", err)
	}
	defer conn.Close()

	err = conn.Bind(bindDN, password)
	if err != nil {
		return false, nil // Auth failed at LLDAP
	}
	return true, nil
}

// PingLLDAP checks if LLDAP is reachable.
func (p *LDAPProxy) PingLLDAP() error {
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", p.lldapAddr))
	if err != nil {
		return err
	}
	conn.Close()
	return nil
}
