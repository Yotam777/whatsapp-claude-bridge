package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestOneShotApprovalSendsOnce(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","owner":["972500000001@s.whatsapp.net"],"control_group":"1203630000@g.us"}`), 0644)
	who := types.NewJID("972500000009", types.DefaultUserServer)
	oneShotApproval.Store(who.String(), true)
	if d, _ := checkPolicy(who); d != "send" {
		t.Fatalf("an approved draft must be sent, got %q", d)
	}
	if d, _ := checkPolicy(who); d != "draft" {
		t.Fatalf("one approval = one message: the next send must be a draft again, got %q", d)
	}
}

func TestOneShotApprovalDoesNotBeatPause(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","paused_until":"2999-01-01T00:00:00Z"}`), 0644)
	who := types.NewJID("972500000008", types.DefaultUserServer)
	oneShotApproval.Store(who.String(), true)
	defer oneShotApproval.Delete(who.String())
	if d, _ := checkPolicy(who); d != "paused" {
		t.Fatalf("an approval must not bypass a pause, got %q", d)
	}
}

func TestDraftCardText(t *testing.T) {
	who := types.NewJID("972500000009", types.DefaultUserServer)
	c := draftCardText(21, "דני", who, "שלום עולם", "")
	lines := strings.Split(c, "\n")
	if !strings.Contains(lines[0], "#21") || !strings.Contains(lines[0], "דני") {
		t.Fatalf("first line must carry the draft number and recipient: %q", lines[0])
	}
	if !strings.Contains(lines[1], "לחיצה ארוכה") || !strings.Contains(lines[1], "✅") {
		t.Fatalf("second line must explain how to approve: %q", lines[1])
	}
	if !strings.Contains(c, "https://wa.me/972500000009?text=%D7%A9%D7%9C%D7%95%D7%9D%20") {
		t.Fatalf("missing wa.me link with %%20 spaces:\n%s", c)
	}
	if !strings.Contains(c, "שלום עולם") {
		t.Fatal("the message text must be in the card")
	}
	g := draftCardText(0, "קבוצה", types.NewJID("1203630000", types.GroupServer), "x", "C:/f.jpg")
	if strings.Contains(g, "wa.me/1203630000?text") || !strings.Contains(g, "📎") {
		t.Fatalf("group/media card must not carry a prefilled link and must mention the file:\n%s", g)
	}
}
