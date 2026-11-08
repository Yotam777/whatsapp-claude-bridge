package main

// owner_token: a second API password for the owner's own surfaces (dashboard, agent). The bridge accepts it exactly like
// api_token — no endpoint is restricted to it.

import (
	"os"
	"strings"
)

var ownerToken string

func loadOwnerToken() string {
	path := "store/owner_token"
	if b, err := os.ReadFile(path); err == nil && len(strings.TrimSpace(string(b))) >= 16 {
		return strings.TrimSpace(string(b))
	}
	t := newSecret()
	os.WriteFile(path, []byte(t), 0600)
	return t
}
