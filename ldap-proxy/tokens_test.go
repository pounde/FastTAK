package main

import (
	"os"
	"testing"
	"time"
)

func tempDB(t *testing.T) *TokenStore {
	t.Helper()
	f, err := os.CreateTemp("", "tokens-*.db")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	t.Cleanup(func() { os.Remove(f.Name()) })
	ts, err := NewTokenStore(f.Name())
	if err != nil {
		t.Fatal(err)
	}
	return ts
}

func TestCreateAndVerify(t *testing.T) {
	ts := tempDB(t)
	plaintext, err := ts.Create("jsmith", 15, false)
	if err != nil {
		t.Fatal(err)
	}
	if plaintext == "" {
		t.Fatal("expected non-empty token")
	}
	ok, err := ts.Verify("jsmith", plaintext)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("expected verify to succeed")
	}
}

func TestOneTimeTokenConsumed(t *testing.T) {
	ts := tempDB(t)
	plaintext, _ := ts.Create("jsmith", 15, true)

	ok, _ := ts.Verify("jsmith", plaintext)
	if !ok {
		t.Fatal("first verify should succeed")
	}
	ok, _ = ts.Verify("jsmith", plaintext)
	if ok {
		t.Fatal("second verify should fail (one-time)")
	}
}

func TestExpiredTokenRejected(t *testing.T) {
	ts := tempDB(t)
	// Create with 0 TTL (no expiry) — should work
	plaintext, _ := ts.Create("jsmith", 0, false)
	ok, _ := ts.Verify("jsmith", plaintext)
	if !ok {
		t.Fatal("no-expiry token should verify")
	}

	// Manually insert an expired token
	hash := hashToken(plaintext + "expired")
	ts.db.Exec(
		"INSERT INTO tokens (username, token_hash, expires_at, one_time, created_at) VALUES (?, ?, ?, 0, ?)",
		"expired_user", hash, time.Now().Add(-1*time.Hour).Unix(), time.Now().Unix(),
	)
	ok, _ = ts.Verify("expired_user", plaintext+"expired")
	if ok {
		t.Fatal("expired token should fail")
	}
}

func TestWrongPasswordRejected(t *testing.T) {
	ts := tempDB(t)
	ts.Create("jsmith", 15, false)
	ok, _ := ts.Verify("jsmith", "wrong-password")
	if ok {
		t.Fatal("wrong password should fail")
	}
}

func TestWrongUsernameRejected(t *testing.T) {
	ts := tempDB(t)
	plaintext, _ := ts.Create("jsmith", 15, false)
	ok, _ := ts.Verify("otheruser", plaintext)
	if ok {
		t.Fatal("wrong username should fail")
	}
}

func TestDeleteByUsername(t *testing.T) {
	ts := tempDB(t)
	plaintext, _ := ts.Create("jsmith", 15, false)
	ts.Create("jsmith", 15, false) // second token
	count, err := ts.DeleteByUsername("jsmith")
	if err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("expected 2 deleted, got %d", count)
	}
	ok, _ := ts.Verify("jsmith", plaintext)
	if ok {
		t.Fatal("deleted token should fail")
	}
}

func TestGetActive(t *testing.T) {
	ts := tempDB(t)
	ts.Create("jsmith", 15, false)
	active, err := ts.GetActive("jsmith")
	if err != nil {
		t.Fatal(err)
	}
	if len(active) != 1 {
		t.Fatalf("expected 1 active token, got %d", len(active))
	}
	if active[0].Username != "jsmith" {
		t.Fatalf("expected username jsmith, got %s", active[0].Username)
	}
}
