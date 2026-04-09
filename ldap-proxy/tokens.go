package main

import (
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

type TokenInfo struct {
	ID        int64
	Username  string
	Token     string
	ExpiresAt *int64
	OneTime   bool
	CreatedAt int64
}

type TokenStore struct {
	db *sql.DB
}

func NewTokenStore(dbPath string) (*TokenStore, error) {
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS tokens (
			id INTEGER PRIMARY KEY,
			username TEXT NOT NULL,
			token_hash TEXT NOT NULL,
			token_plain TEXT NOT NULL,
			expires_at INTEGER,
			one_time BOOLEAN NOT NULL DEFAULT 0,
			created_at INTEGER NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_tokens_username ON tokens(username);
	`)
	if err != nil {
		return nil, fmt.Errorf("create table: %w", err)
	}
	return &TokenStore{db: db}, nil
}

func hashToken(token string) string {
	h := sha256.Sum256([]byte(token))
	return hex.EncodeToString(h[:])
}

func generateToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// Create generates a random token, stores its hash, and returns the plaintext.
// ttlMinutes=0 means no expiry.
func (ts *TokenStore) Create(username string, ttlMinutes int, oneTime bool) (string, error) {
	plaintext, err := generateToken()
	if err != nil {
		return "", fmt.Errorf("generate token: %w", err)
	}
	hash := hashToken(plaintext)
	now := time.Now().Unix()

	var expiresAt *int64
	if ttlMinutes > 0 {
		exp := now + int64(ttlMinutes)*60
		expiresAt = &exp
	}

	_, err = ts.db.Exec(
		"INSERT INTO tokens (username, token_hash, token_plain, expires_at, one_time, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		username, hash, plaintext, expiresAt, oneTime, now,
	)
	if err != nil {
		return "", fmt.Errorf("insert token: %w", err)
	}
	return plaintext, nil
}

// Verify checks if the password matches a valid token for the given username.
// Returns true if matched. Deletes one-time tokens on match. Cleans up expired tokens.
// Uses a transaction to prevent concurrent consumption of one-time tokens.
func (ts *TokenStore) Verify(username, password string) (bool, error) {
	hash := hashToken(password)
	now := time.Now().Unix()

	// Clean expired tokens first
	ts.db.Exec("DELETE FROM tokens WHERE expires_at IS NOT NULL AND expires_at <= ?", now)

	tx, err := ts.db.Begin()
	if err != nil {
		return false, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	var id int64
	var oneTime bool
	err = tx.QueryRow(
		"SELECT id, one_time FROM tokens WHERE username = ? AND token_hash = ? AND (expires_at IS NULL OR expires_at > ?)",
		username, hash, now,
	).Scan(&id, &oneTime)

	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("query token: %w", err)
	}

	if oneTime {
		if _, err := tx.Exec("DELETE FROM tokens WHERE id = ?", id); err != nil {
			return false, fmt.Errorf("consume one-time token: %w", err)
		}
	}

	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("commit tx: %w", err)
	}
	return true, nil
}

// DeleteByUsername removes all tokens for a user. Returns count deleted.
func (ts *TokenStore) DeleteByUsername(username string) (int64, error) {
	result, err := ts.db.Exec("DELETE FROM tokens WHERE username = ?", username)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

// GetActive returns all non-expired tokens for a username.
func (ts *TokenStore) GetActive(username string) ([]TokenInfo, error) {
	now := time.Now().Unix()
	rows, err := ts.db.Query(
		"SELECT id, username, token_plain, expires_at, one_time, created_at FROM tokens WHERE username = ? AND (expires_at IS NULL OR expires_at > ?)",
		username, now,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tokens []TokenInfo
	for rows.Next() {
		var t TokenInfo
		if err := rows.Scan(&t.ID, &t.Username, &t.Token, &t.ExpiresAt, &t.OneTime, &t.CreatedAt); err != nil {
			return nil, err
		}
		tokens = append(tokens, t)
	}
	return tokens, nil
}

// Ping checks if the database is accessible.
func (ts *TokenStore) Ping() error {
	return ts.db.Ping()
}
