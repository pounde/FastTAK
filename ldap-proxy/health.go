package main

import (
	"encoding/json"
	"net/http"
)

type HealthHandler struct {
	tokens *TokenStore
	proxy  *LDAPProxy
}

func NewHealthHandler(tokens *TokenStore, proxy *LDAPProxy) *HealthHandler {
	return &HealthHandler{tokens: tokens, proxy: proxy}
}

func (h *HealthHandler) HandleHealthz(w http.ResponseWriter, r *http.Request) {
	status := map[string]string{}
	healthy := true

	if err := h.tokens.Ping(); err != nil {
		status["sqlite"] = err.Error()
		healthy = false
	} else {
		status["sqlite"] = "ok"
	}

	if err := h.proxy.PingLLDAP(); err != nil {
		status["lldap"] = err.Error()
		healthy = false
	} else {
		status["lldap"] = "ok"
	}

	w.Header().Set("Content-Type", "application/json")
	if !healthy {
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	json.NewEncoder(w).Encode(status)
}
