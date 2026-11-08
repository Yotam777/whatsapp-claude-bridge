package main

import (
	"os"
	"path/filepath"
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

// auto mode is gone (owner's rule); "allow" still lets one approved recipient through (CLAUDE.md rule 4).
func TestAutoIgnoredAllowHonoured(t *testing.T) {
	policyDir(t, `{"mode":"auto","auto_until":"","allow":["972500000009"],"owner":["972500000001"],"control_group":"1203630000@g.us"}`)
	if d, _ := checkPolicy(types.NewJID("972500000008", types.DefaultUserServer)); d != "draft" {
		t.Fatalf("auto mode must not send directly, got %q", d)
	}
	if d, _ := checkPolicy(types.NewJID("972500000009", types.DefaultUserServer)); d != "send" {
		t.Fatalf("an allowed recipient is sent to, got %q", d)
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
