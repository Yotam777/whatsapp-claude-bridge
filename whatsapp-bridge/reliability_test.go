package main

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
)

func memStore(t *testing.T) *MessageStore {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
		CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN,
			media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER,
			edited INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, PRIMARY KEY (id, chat_jid));
		CREATE TABLE send_log (id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT, message_id TEXT, ok BOOLEAN, detail TEXT, timestamp TIMESTAMP);`)
	if err != nil {
		t.Fatal(err)
	}
	return &MessageStore{db: db}
}

// A re-delivered message (history sync / retry) must not undo a recorded edit or delete.
func TestStoreMessageKeepsEditAndDelete(t *testing.T) {
	s := memStore(t)
	now := time.Now()
	s.StoreMessage("m1", "c@g.us", "u", "original", now, false, "", "", "", nil, nil, nil, 0)
	s.db.Exec("UPDATE messages SET content = 'edited text', edited = 1 WHERE id = 'm1'")
	s.StoreMessage("m1", "c@g.us", "u", "original", now, false, "", "", "", nil, nil, nil, 0)
	var content string
	var edited int
	s.db.QueryRow("SELECT content, edited FROM messages WHERE id = 'm1'").Scan(&content, &edited)
	if content != "edited text" || edited != 1 {
		t.Fatalf("edit was undone: %q edited=%d", content, edited)
	}
	s.db.Exec("UPDATE messages SET deleted = 1 WHERE id = 'm1'")
	s.StoreMessage("m1", "c@g.us", "u", "original", now, false, "", "", "", nil, nil, nil, 0)
	var deleted int
	s.db.QueryRow("SELECT deleted FROM messages WHERE id = 'm1'").Scan(&deleted)
	if deleted != 1 {
		t.Fatal("delete was undone by a re-delivery")
	}
	// a normal (never edited) message still updates
	s.StoreMessage("m2", "c@g.us", "u", "a", now, false, "", "", "", nil, nil, nil, 0)
	s.StoreMessage("m2", "c@g.us", "u", "b", now, false, "", "", "", nil, nil, nil, 0)
	s.db.QueryRow("SELECT content FROM messages WHERE id = 'm2'").Scan(&content)
	if content != "b" {
		t.Fatalf("plain upsert should update content, got %q", content)
	}
}

// Daily caps survive a restart and count only sends to other people.
func TestTodaySendsCountsOthersOnly(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","owner":["972500000001@s.whatsapp.net"],"control_group":"1203630000@g.us","control_groups":["1203631111@g.us"]}`), 0644)
	prev := globalStore
	defer func() { globalStore = prev }()
	globalStore = memStore(t)
	now := time.Now()
	for _, r := range []string{"1203630000@g.us", "1203631111@g.us", "972500000001@s.whatsapp.net", "972500000009@s.whatsapp.net", "972500000009@s.whatsapp.net"} {
		globalStore.db.Exec("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES (?, 'x', 1, '', ?)", r, now)
	}
	globalStore.db.Exec("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES ('972500000007@s.whatsapp.net', '', 0, 'fail', ?)", now)
	n, fresh := todaySends()
	if n != 2 {
		t.Fatalf("expected 2 sends to others today, got %d", n)
	}
	if !fresh["972500000009@s.whatsapp.net"] || len(fresh) != 1 {
		t.Fatalf("new-chat set wrong: %v", fresh)
	}
	if !isOwnTarget(types.NewJID("1203631111", types.GroupServer)) {
		t.Fatal("connected control group must count as own")
	}
}

// Contact and forward drafts rebuild the real message (not the text description).
func TestBuildSpecial(t *testing.T) {
	msg, desc, err := buildSpecial(nil, "contact", specialPayload{Name: "Dana", Phone: "972500000009"})
	if err != nil || msg.GetContactMessage() == nil || !strings.Contains(msg.GetContactMessage().GetVcard(), "waid=972500000009") {
		t.Fatalf("contact not built: %v %v", msg, err)
	}
	if !strings.Contains(desc, "Dana") {
		t.Fatalf("bad description %q", desc)
	}
	msg, _, err = buildSpecial(nil, "forward", specialPayload{Text: "hello"})
	if err != nil || !msg.GetExtendedTextMessage().GetContextInfo().GetIsForwarded() {
		t.Fatal("forward must be marked as forwarded")
	}
	if _, _, err = buildSpecial(nil, "poll", specialPayload{Question: "q", Options: []string{"one"}}); err == nil {
		t.Fatal("poll with one option must be rejected")
	}
	if _, _, err = buildSpecial(nil, "status", specialPayload{Text: "x"}); err == nil {
		t.Fatal("unknown kind must be rejected")
	}
}
