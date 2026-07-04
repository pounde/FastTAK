package main

import (
	"sync"
	"testing"
)

func TestConnAuthLifecycle(t *testing.T) {
	a := newConnAuth()

	if a.isAuthed(1) {
		t.Fatal("unknown connection should not be authed")
	}

	a.setAuthed(1, true)
	if !a.isAuthed(1) {
		t.Fatal("connection should be authed after setAuthed(true)")
	}

	a.setAuthed(1, false)
	if a.isAuthed(1) {
		t.Fatal("connection should not be authed after setAuthed(false)")
	}

	a.setAuthed(2, true)
	a.forget(2)
	if a.isAuthed(2) {
		t.Fatal("connection should not be authed after forget")
	}
}

// Race detector coverage: concurrent access must be safe.
func TestConnAuthConcurrent(t *testing.T) {
	a := newConnAuth()
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			a.setAuthed(id, true)
			_ = a.isAuthed(id)
			a.forget(id)
		}(i)
	}
	wg.Wait()
}
