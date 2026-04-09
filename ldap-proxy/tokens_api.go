package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

type TokensAPI struct {
	store          *TokenStore
	defaultTTL     int
	defaultOneTime bool
	mux            *http.ServeMux
}

type createTokenRequest struct {
	Username   string `json:"username"`
	TTLMinutes *int   `json:"ttl_minutes,omitempty"`
	OneTime    *bool  `json:"one_time,omitempty"`
}

type createTokenResponse struct {
	Token     string `json:"token"`
	ExpiresAt *int64 `json:"expires_at"`
	OneTime   bool   `json:"one_time"`
	Username  string `json:"username"`
}

type activeTokensResponse struct {
	Tokens []tokenEntry `json:"tokens"`
}

type tokenEntry struct {
	ID        int64  `json:"id"`
	Token     string `json:"token"`
	ExpiresAt *int64 `json:"expires_at"`
	OneTime   bool   `json:"one_time"`
	CreatedAt int64  `json:"created_at"`
}

func NewTokensAPI(store *TokenStore, defaultTTL int, defaultOneTime bool) *TokensAPI {
	api := &TokensAPI{
		store:          store,
		defaultTTL:     defaultTTL,
		defaultOneTime: defaultOneTime,
		mux:            http.NewServeMux(),
	}
	api.mux.HandleFunc("POST /tokens", api.handleCreate)
	api.mux.HandleFunc("GET /tokens/{username}", api.handleGetActive)
	api.mux.HandleFunc("DELETE /tokens/{username}", api.handleDelete)
	return api
}

func (a *TokensAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	a.mux.ServeHTTP(w, r)
}

func (a *TokensAPI) handleCreate(w http.ResponseWriter, r *http.Request) {
	var req createTokenRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error": "invalid JSON"}`, http.StatusBadRequest)
		return
	}
	if strings.TrimSpace(req.Username) == "" {
		http.Error(w, `{"error": "username required"}`, http.StatusBadRequest)
		return
	}

	ttl := a.defaultTTL
	if req.TTLMinutes != nil {
		ttl = *req.TTLMinutes
	}
	oneTime := a.defaultOneTime
	if req.OneTime != nil {
		oneTime = *req.OneTime
	}

	plaintext, err := a.store.Create(req.Username, ttl, oneTime)
	if err != nil {
		http.Error(w, `{"error": "internal error"}`, http.StatusInternalServerError)
		return
	}

	var expiresAt *int64
	if ttl > 0 {
		exp := time.Now().Unix() + int64(ttl)*60
		expiresAt = &exp
	}

	resp := createTokenResponse{
		Token:     plaintext,
		ExpiresAt: expiresAt,
		OneTime:   oneTime,
		Username:  req.Username,
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(resp)
}

func (a *TokensAPI) handleGetActive(w http.ResponseWriter, r *http.Request) {
	username := r.PathValue("username")
	active, err := a.store.GetActive(username)
	if err != nil {
		http.Error(w, `{"error": "internal error"}`, http.StatusInternalServerError)
		return
	}

	entries := make([]tokenEntry, 0, len(active))
	for _, t := range active {
		entries = append(entries, tokenEntry{
			ID:        t.ID,
			Token:     t.Token,
			ExpiresAt: t.ExpiresAt,
			OneTime:   t.OneTime,
			CreatedAt: t.CreatedAt,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(activeTokensResponse{Tokens: entries})
}

func (a *TokensAPI) handleDelete(w http.ResponseWriter, r *http.Request) {
	username := r.PathValue("username")
	count, err := a.store.DeleteByUsername(username)
	if err != nil {
		http.Error(w, `{"error": "internal error"}`, http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int64{"deleted": count})
}
