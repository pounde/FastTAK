package main

// LDAP server library choice: github.com/jimlambrt/gldap
//
// Evaluated options:
//   - jimlambrt/gldap: Clean handler-based API (Mux + HandlerFunc), supports
//     bind/search/unbind natively, handles concurrent connections, actively
//     maintained. Works well alongside go-ldap/ldap/v3 for upstream forwarding.
//   - vjeantet/ldapserver: Older, less maintained, similar handler model but
//     fewer abstractions and rougher API.
//   - Raw net.Conn + BER: Maximum control but enormous implementation effort
//     for a proxy that just needs to intercept binds and relay searches.
//
// gldap wins on maintenance, API ergonomics, and the fact that we only need
// to intercept bind and forward everything else. The handler model maps
// directly to our proxy architecture.

import (
	"fmt"
	"log"

	"github.com/jimlambrt/gldap"

	ldap "github.com/go-ldap/ldap/v3"
)

// startLDAPServer starts the LDAP protocol listener and blocks until the
// server is stopped or encounters a fatal error.
func startLDAPServer(listenAddr string, proxy *LDAPProxy) error {
	s, err := gldap.NewServer()
	if err != nil {
		return fmt.Errorf("create gldap server: %w", err)
	}

	r, err := gldap.NewMux()
	if err != nil {
		return fmt.Errorf("create gldap mux: %w", err)
	}

	// Bind: intercept with proxy.HandleBind (token store → LLDAP fallback)
	if err := r.Bind(makeBindHandler(proxy)); err != nil {
		return fmt.Errorf("register bind handler: %w", err)
	}

	// Search: forward to LLDAP, relay results back to the client
	if err := r.Search(makeSearchHandler(proxy)); err != nil {
		return fmt.Errorf("register search handler: %w", err)
	}

	// Unbind: just log, gldap handles connection cleanup
	if err := r.Unbind(makeUnbindHandler()); err != nil {
		return fmt.Errorf("register unbind handler: %w", err)
	}

	// Default route: reject unsupported operations gracefully
	if err := r.DefaultRoute(makeDefaultHandler()); err != nil {
		return fmt.Errorf("register default handler: %w", err)
	}

	if err := s.Router(r); err != nil {
		return fmt.Errorf("set router: %w", err)
	}

	return s.Run(listenAddr)
}

// makeBindHandler returns a gldap handler that authenticates bind requests
// through the proxy (token check first, then LLDAP fallback).
func makeBindHandler(proxy *LDAPProxy) gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		resp := r.NewBindResponse(
			gldap.WithResponseCode(gldap.ResultInvalidCredentials),
		)
		defer func() {
			_ = w.Write(resp)
		}()

		m, err := r.GetSimpleBindMessage()
		if err != nil {
			log.Printf("[conn=%d] bind: not a simple bind message: %v", r.ConnectionID(), err)
			return
		}

		// Anonymous bind (empty DN and password) — allow it
		if m.UserName == "" && m.Password == "" {
			resp.SetResultCode(gldap.ResultSuccess)
			log.Printf("[conn=%d] bind: anonymous bind allowed", r.ConnectionID())
			return
		}

		ok, err := proxy.HandleBind(m.UserName, string(m.Password))
		if err != nil {
			log.Printf("[conn=%d] bind: error for %s: %v", r.ConnectionID(), m.UserName, err)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("internal proxy error")
			return
		}

		if ok {
			resp.SetResultCode(gldap.ResultSuccess)
			log.Printf("[conn=%d] bind: success for %s", r.ConnectionID(), m.UserName)
		} else {
			log.Printf("[conn=%d] bind: invalid credentials for %s", r.ConnectionID(), m.UserName)
		}
	}
}

// makeSearchHandler returns a gldap handler that forwards search requests
// to LLDAP and relays the results back to the client.
func makeSearchHandler(proxy *LDAPProxy) gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		resp := r.NewSearchDoneResponse()
		defer func() {
			_ = w.Write(resp)
		}()

		m, err := r.GetSearchMessage()
		if err != nil {
			log.Printf("[conn=%d] search: bad message: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultOperationsError)
			return
		}

		log.Printf("[conn=%d] search: base=%q scope=%d filter=%q", r.ConnectionID(), m.BaseDN, m.Scope, m.Filter)

		// Connect to LLDAP as admin to perform the search
		conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", proxy.lldapAddr))
		if err != nil {
			log.Printf("[conn=%d] search: connect to lldap: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultUnavailable)
			resp.SetDiagnosticMessage("upstream LDAP unavailable")
			return
		}
		defer conn.Close()

		// Bind as admin for the search
		if err := conn.Bind(proxy.adminBindDN, proxy.adminBindPass); err != nil {
			log.Printf("[conn=%d] search: admin bind to lldap: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("upstream bind failed")
			return
		}

		// Build the upstream search request, faithfully forwarding all parameters
		searchReq := ldap.NewSearchRequest(
			m.BaseDN,
			int(m.Scope),
			int(m.DerefAliases),
			int(m.SizeLimit),
			int(m.TimeLimit),
			m.TypesOnly,
			m.Filter,
			m.Attributes,
			nil,
		)

		sr, err := conn.Search(searchReq)
		if err != nil {
			log.Printf("[conn=%d] upstream search error: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("upstream search failed")
			return
		}

		// Relay each entry back to the client
		for _, entry := range sr.Entries {
			attrs := make(map[string][]string, len(entry.Attributes))
			for _, a := range entry.Attributes {
				attrs[a.Name] = a.Values
			}
			e := r.NewSearchResponseEntry(entry.DN, gldap.WithAttributes(attrs))
			_ = w.Write(e)
		}

		resp.SetResultCode(gldap.ResultSuccess)
	}
}

// makeUnbindHandler returns a gldap handler for unbind requests.
func makeUnbindHandler() gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		log.Printf("[conn=%d] unbind", r.ConnectionID())
		// Nothing to do — gldap handles connection cleanup
	}
}

// makeDefaultHandler returns a handler for any unsupported LDAP operations.
// TAK Server only uses bind + search, so other operations are rejected.
func makeDefaultHandler() gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		log.Printf("[conn=%d] unsupported operation, rejecting", r.ConnectionID())
		resp := r.NewResponse(
			gldap.WithResponseCode(gldap.ResultUnwillingToPerform),
			gldap.WithDiagnosticMessage("operation not supported by proxy"),
		)
		_ = w.Write(resp)
	}
}
