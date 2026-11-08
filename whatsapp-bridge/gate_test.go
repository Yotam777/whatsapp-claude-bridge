package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

func policyDir(t *testing.T, pol string) {
	t.Helper()
	dir := t.TempDir()
	wd, _ := os.Getwd()
	t.Cleanup(func() { os.Chdir(wd) })
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"), []byte(pol), 0644)
}

// auto mode and the standing allow-list are gone: only an explicit approval sends to others.
func TestAutoAndAllowAreIgnored(t *testing.T) {
	policyDir(t, `{"mode":"auto","auto_until":"","allow":["972500000009"],"owner":["972500000001"],"control_group":"1203630000@g.us"}`)
	if d, _ := checkPolicy(types.NewJID("972500000009", types.DefaultUserServer)); d != "draft" {
		t.Fatalf("allow/auto must not send directly any more, got %q", d)
	}
}

func TestOwnerOnlyAndTokens(t *testing.T) {
	policyDir(t, `{"mode":"draft","owner":["972500000001"],"control_group":"1203630000@g.us"}`)
	apiToken, ownerToken = "api-token-aaaaaaaaaaaa", "owner-token-bbbbbbbbbb"
	defer func() { apiToken, ownerToken = "", "" }()
	ok := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"success":true}`)) })
	h := withAuth(ok)
	do := func(method, path, tok, body string) int {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+tok)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec.Code
	}
	if c := do("POST", "/api/draft_decide", apiToken, `{"id":1,"approve":true}`); c != http.StatusForbidden {
		t.Fatalf("Claude's token must not approve drafts, got %d", c)
	}
	if c := do("POST", "/api/policy", apiToken, `{"control_groups":["x@g.us"]}`); c != http.StatusForbidden {
		t.Fatalf("Claude's token must not change the policy, got %d", c)
	}
	if c := do("GET", "/api/policy", apiToken, ""); c != 200 {
		t.Fatalf("reading the policy stays open, got %d", c)
	}
	if c := do("POST", "/api/draft_decide", ownerToken, `{"id":1,"approve":true}`); c != 200 {
		t.Fatalf("owner token must approve, got %d", c)
	}
	if c := do("POST", "/api/send", "wrong", `{}`); c != http.StatusUnauthorized {
		t.Fatalf("bad token must be rejected, got %d", c)
	}
}

// Gated actions decide by target: the owner's chats run directly, anyone else needs a card.
func TestGateDecisions(t *testing.T) {
	policyDir(t, `{"mode":"draft","owner":["972500000001"],"control_group":"1203630000@g.us","control_groups":["1203631111@g.us"]}`)
	registerGates()
	cases := []struct {
		path string
		body map[string]any
		need bool
	}{
		{"/api/react", map[string]any{"chat_jid": "1203630000@g.us", "message_id": "X", "emoji": "👀"}, false},
		{"/api/react", map[string]any{"chat_jid": "1203639999@g.us", "message_id": "X", "emoji": "👍"}, true},
		{"/api/edit_message", map[string]any{"jid": "972500000009@s.whatsapp.net", "id": "X", "text": "t"}, true},
		{"/api/edit_message", map[string]any{"jid": "1203631111@g.us", "id": "X", "text": "t"}, false},
		{"/api/delete_message", map[string]any{"jid": "972500000009@s.whatsapp.net", "id": "X"}, true},
		{"/api/post_status", map[string]any{"text": "hi"}, true},
		{"/api/set_about", map[string]any{"text": "hi"}, true},
		{"/api/set_group", map[string]any{"jid": "1203630000@g.us", "name": "x"}, false},
		{"/api/set_group", map[string]any{"jid": "1203639999@g.us", "description": "x"}, true},
		{"/api/group_participants", map[string]any{"jid": "1203630000@g.us", "action": "add", "users": []any{"9725"}}, true},
		{"/api/group_invite", map[string]any{"jid": "1203639999@g.us"}, false},
		{"/api/group_invite", map[string]any{"jid": "1203639999@g.us", "reset": true}, true},
		{"/api/create_group", map[string]any{"name": "x"}, false},
		{"/api/create_group", map[string]any{"name": "x", "participants": []any{"9725"}}, true},
		{"/api/join_group", map[string]any{"link": "https://chat.whatsapp.com/X"}, true},
		{"/api/block", map[string]any{"jid": "972500000009"}, true},
	}
	for _, c := range cases {
		_, _, need := gatedPaths[c.path](c.body)
		if need != c.need {
			t.Errorf("%s %v: need=%v, want %v", c.path, c.body, need, c.need)
		}
	}
	// the owner token bypasses the gate (dashboard / approved replay)
	req := httptest.NewRequest("POST", "/api/block", strings.NewReader(`{"jid":"972500000009"}`))
	if gateRequest(httptest.NewRecorder(), req, true) {
		t.Fatal("owner requests must not be gated")
	}
}

func TestSentID(t *testing.T) {
	if id := sentID("Message sent to 1203630000@g.us id=3EB0ABC123"); id != "3EB0ABC123" {
		t.Fatalf("got %q", id)
	}
	if sentID("Not connected") != "" {
		t.Fatal("no id expected")
	}
}

func TestQuotedStanza(t *testing.T) {
	m := &waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("מאשר לך שליחה"),
		ContextInfo: &waProto.ContextInfo{StanzaID: proto.String("CARD1")}}}
	if q := quotedStanza(m); q != "CARD1" {
		t.Fatalf("got %q", q)
	}
	if quotedStanza(&waProto.Message{Conversation: proto.String("x")}) != "" || quotedStanza(nil) != "" {
		t.Fatal("plain / nil message has no quote")
	}
}
