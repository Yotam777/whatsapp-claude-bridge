package main

// Send policy and account-health guard.
//
// Draft mode (default): the bridge only sends to the owner's own chats. A send to anyone else becomes a
// draft card in the owner's control group with a wa.me link that opens that chat with the text pre-typed,
// so the owner reviews and presses send. Auto mode (explicit permission, optionally time-limited) sends directly.
//
// Health guard: a temporary ban, a reach-out/capping error or a rate-limit answer pauses every send to
// other people, so the tool backs off instead of digging the account deeper.

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

const policyFile = "store/send_policy.json"

type SendPolicy struct {
	Mode         string   `json:"mode"`          // "draft" or "auto"
	AutoUntil    string   `json:"auto_until"`    // RFC3339; auto mode falls back to draft after this
	Owner        []string `json:"owner"`         // the owner's own chats: always sent directly
	ControlGroup string   `json:"control_group"` // where draft cards and alerts go
	Allow        []string `json:"allow"`         // extra recipients that may always be sent to directly
	PausedUntil  string   `json:"paused_until"`  // set by the health guard
	PauseReason  string   `json:"pause_reason"`
}

var policyMu sync.Mutex

func defaultPolicy() SendPolicy {
	p := SendPolicy{Mode: "draft", ControlGroup: os.Getenv("WA_CONTROL_GROUP")}
	if o := os.Getenv("WA_OWNER"); o != "" {
		p.Owner = strings.Split(o, ",")
	}
	if p.ControlGroup == "" && globalClient != nil && globalClient.Store.ID != nil {
		p.ControlGroup = globalClient.Store.ID.ToNonAD().String() // the owner's chat with himself
	}
	return p
}

// isOwnChat: the account's own chat (by phone or LID) always counts as the owner.
func isOwnChat(jid types.JID) bool {
	if globalClient == nil || globalClient.Store.ID == nil {
		return false
	}
	return jid.User == globalClient.Store.ID.User || (!globalClient.Store.LID.IsEmpty() && jid.User == globalClient.Store.LID.User)
}

func loadPolicy() SendPolicy {
	p := defaultPolicy()
	if b, err := os.ReadFile(policyFile); err == nil {
		json.Unmarshal(b, &p)
	}
	return p
}

func savePolicy(p SendPolicy) {
	b, _ := json.MarshalIndent(p, "", "  ")
	os.WriteFile(policyFile, b, 0644)
}

func inList(list []string, jid types.JID) bool {
	for _, s := range list {
		if s == jid.String() || s == jid.User {
			return true
		}
	}
	return false
}

// checkPolicy decides how a send to jid is handled: "send", "draft", or "paused".
func checkPolicy(jid types.JID) (string, SendPolicy) {
	policyMu.Lock()
	defer policyMu.Unlock()
	p := loadPolicy()
	own := inList(p.Owner, jid) || jid.String() == p.ControlGroup || isOwnChat(jid)
	if own {
		return "send", p
	}
	if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && time.Now().Before(t) {
		return "paused", p
	}
	if inList(p.Allow, jid) {
		return "send", p
	}
	if p.Mode == "auto" {
		if t, err := time.Parse(time.RFC3339, p.AutoUntil); p.AutoUntil == "" || (err == nil && time.Now().Before(t)) {
			return "send", p
		}
	}
	return "draft", p
}

// pauseSends stops all sends to other people for d and alerts the owner.
func pauseSends(client *whatsmeow.Client, d time.Duration, reason string) {
	policyMu.Lock()
	p := loadPolicy()
	until := time.Now().Add(d)
	if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && t.After(until) {
		until = t
	}
	p.PausedUntil, p.PauseReason = until.Format(time.RFC3339), reason
	savePolicy(p)
	policyMu.Unlock()
	fmt.Printf("SENDS PAUSED until %s: %s\n", until.Format("2006-01-02 15:04"), reason)
	if client != nil && client.IsConnected() {
		go sendWhatsAppMessage(client, p.ControlGroup, fmt.Sprintf("🤖 ⛔ שליחה לאחרים הושהתה עד %s.\nסיבה: %s\nהשיחה איתך ממשיכה כרגיל.",
			until.Format("02.01 15:04"), reason), "")
	}
}

// looksLikeBlock recognises send errors that mean WhatsApp is throttling or restricting the account.
func looksLikeBlock(err error) bool {
	s := strings.ToLower(err.Error())
	for _, k := range []string{"463", "475", "429", "rate-overlimit", "rate overlimit", "too many", "blocked", "spam", "not-authorized", "403"} {
		if strings.Contains(s, k) {
			return true
		}
	}
	return false
}

// makeDraft turns a send into a draft card in the control group instead of sending it.
func makeDraft(client *whatsmeow.Client, p SendPolicy, jid types.JID, message, mediaPath, quotedID string) (bool, string) {
	name := jid.User
	if globalStore != nil {
		var n string
		globalStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", jid.String()).Scan(&n)
		if n != "" && n != jid.User {
			name = n
		}
	}
	if c, err := client.Store.Contacts.GetContact(contextBG(), jid); err == nil && c.FullName != "" {
		name = c.FullName
	}
	if globalStore != nil {
		globalStore.db.Exec(`INSERT INTO drafts (recipient, name, message, media_path, created, status) VALUES (?, ?, ?, ?, ?, 'pending')`,
			jid.String(), name, message, mediaPath, time.Now())
	}
	// best path: type the draft straight into the chat on the owner's phone (nothing is sent)
	if jid.Server == types.DefaultUserServer && mediaPath == "" {
		if err := phoneDraft(jid.User, message); err == nil {
			note := ""
			if quotedID != "" {
				note = "\n(הציטוט לא נשמר בטיוטה בטלפון — אם צריך, החלק על ההודעה המקורית וענה עליה)"
			}
			sendWhatsAppMessage(client, p.ControlGroup, fmt.Sprintf("🤖 📝 טיוטה מוכנה בטלפון בשיחה עם %s — פתח, ערוך ושלח.%s", name, note), "")
			return false, fmt.Sprintf("DRAFT: typed into the chat with %s on the phone (not sent)", name)
		} else {
			fmt.Println("Phone draft unavailable:", err)
		}
	}
	var card strings.Builder
	card.WriteString(fmt.Sprintf("🤖 📝 *טיוטה ממתינה לשליחה* → %s\n", name))
	if jid.Server == types.DefaultUserServer {
		card.WriteString("לחץ על הקישור, ההודעה תיפתח מוכנה בשיחה — בדוק ושלח:\n")
		card.WriteString("https://wa.me/" + jid.User + "?text=" + url.QueryEscape(message) + "\n")
	} else {
		card.WriteString("(קבוצה / מזהה ללא מספר — העתק את הטקסט ושלח ידנית)\n")
	}
	if mediaPath != "" {
		card.WriteString("📎 קובץ מצורף נשלח בנפרד לכאן — העבר אותו ליעד.\n")
	}
	card.WriteString("━━━━━━━━━━\n" + message)
	ok, res := sendWhatsAppMessage(client, p.ControlGroup, card.String(), "")
	if ok && mediaPath != "" {
		sendWhatsAppMessage(client, p.ControlGroup, "🤖 📎 קובץ לטיוטה → "+name, mediaPath)
	}
	if !ok {
		return false, "Draft could not be posted: " + res
	}
	return false, fmt.Sprintf("DRAFT: not sent — draft for %s posted to the control group for approval", name)
}
