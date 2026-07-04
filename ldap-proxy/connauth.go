package main

import "sync"

// connAuth tracks whether each LDAP client connection has completed a
// successful non-anonymous bind. The search handler forwards to LLDAP as the
// admin service account, so without this gate any client (including an
// anonymous one) could subtree-enumerate the entire directory. Searches are
// only relayed for connections that have authenticated.
//
// Keyed by the gldap connection ID. Entries are removed when the connection
// closes, via the server's WithOnClose callback, so the map does not grow
// unbounded.
type connAuth struct {
	mu     sync.RWMutex
	authed map[int]bool
}

func newConnAuth() *connAuth {
	return &connAuth{authed: make(map[int]bool)}
}

// setAuthed records the authentication state for a connection. A non-anonymous
// bind that succeeds sets true; anonymous binds and failures set false.
func (c *connAuth) setAuthed(connID int, authed bool) {
	c.mu.Lock()
	c.authed[connID] = authed
	c.mu.Unlock()
}

// isAuthed reports whether the connection currently holds a successful
// non-anonymous bind.
func (c *connAuth) isAuthed(connID int) bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.authed[connID]
}

// forget drops a connection's state. Call on connection close.
func (c *connAuth) forget(connID int) {
	c.mu.Lock()
	delete(c.authed, connID)
	c.mu.Unlock()
}
