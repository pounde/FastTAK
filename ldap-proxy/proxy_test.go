package main

import "testing"

func TestExtractUsername(t *testing.T) {
	tests := []struct {
		name     string
		bindDN   string
		baseDN   string
		expected string
	}{
		{"valid user", "uid=jsmith,ou=people,dc=takldap", "dc=takldap", "jsmith"},
		{"case insensitive", "UID=jsmith,OU=people,DC=takldap", "dc=takldap", "jsmith"},
		{"old cn format", "cn=jsmith,ou=users,dc=takldap", "dc=takldap", ""},
		{"wrong base DN", "uid=jsmith,ou=people,dc=other", "dc=takldap", ""},
		{"no uid prefix", "ou=people,dc=takldap", "dc=takldap", ""},
		{"empty DN", "", "dc=takldap", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := extractUsername(tt.bindDN, tt.baseDN)
			if result != tt.expected {
				t.Errorf("extractUsername(%q, %q) = %q, want %q", tt.bindDN, tt.baseDN, result, tt.expected)
			}
		})
	}
}
