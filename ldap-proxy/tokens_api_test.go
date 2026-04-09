package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testAPI(t *testing.T) (*TokensAPI, *TokenStore) {
	t.Helper()
	ts := tempDB(t)
	api := NewTokensAPI(ts, 15, true)
	return api, ts
}

func TestCreateTokenEndpoint(t *testing.T) {
	api, _ := testAPI(t)
	body := `{"username": "jsmith", "ttl_minutes": 10, "one_time": true}`
	req := httptest.NewRequest("POST", "/tokens", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["token"] == nil || resp["token"] == "" {
		t.Fatal("expected token in response")
	}
	if resp["expires_at"] == nil {
		t.Fatal("expected expires_at in response")
	}
}

func TestCreateTokenDefaults(t *testing.T) {
	api, _ := testAPI(t)
	body := `{"username": "jsmith"}`
	req := httptest.NewRequest("POST", "/tokens", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["one_time"] != true {
		t.Fatal("expected one_time=true by default")
	}
}

func TestGetActiveTokens(t *testing.T) {
	api, ts := testAPI(t)
	ts.Create("jsmith", 15, false)

	req := httptest.NewRequest("GET", "/tokens/jsmith", nil)
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	tokens := resp["tokens"].([]interface{})
	if len(tokens) != 1 {
		t.Fatalf("expected 1 token, got %d", len(tokens))
	}
}

func TestDeleteTokens(t *testing.T) {
	api, ts := testAPI(t)
	ts.Create("jsmith", 15, false)

	req := httptest.NewRequest("DELETE", "/tokens/jsmith", nil)
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	active, _ := ts.GetActive("jsmith")
	if len(active) != 0 {
		t.Fatal("expected all tokens deleted")
	}
}

func TestGetNoTokens(t *testing.T) {
	api, _ := testAPI(t)
	req := httptest.NewRequest("GET", "/tokens/nobody", nil)
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	tokens := resp["tokens"].([]interface{})
	if len(tokens) != 0 {
		t.Fatalf("expected 0 tokens, got %d", len(tokens))
	}
}
