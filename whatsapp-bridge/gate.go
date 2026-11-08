package main

// Owner token + approval gate.
//
// Two bearer tokens:
//   - store/api_token   — used by Claude's tools (wa_send, wa_admin, MCP server). Everything reaching other people
//     through it is a draft card: one ✅ of the owner = one action.
//   - store/owner_token — used only by the owner's own surfaces (dashboard, the agent's "מאשר לך שליחה" handling,
//     setup). Required to approve drafts and to change the send policy; outward actions with it run directly.
//
// The gate runs in the auth middleware, so it covers every listed endpoint whatever handler style it uses. A gated
// call made with the api token is stored as a draft (kind "api", payload = path + raw body) and a card is posted;
// approving the card replays the original request internally (DefaultServeMux, bypassing the middleware).

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
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

// ownerOnly: endpoints that need the owner token (approving drafts, changing the policy). GET /api/policy stays open.
func ownerOnly(r *http.Request) bool {
	switch r.URL.Path {
	case "/api/draft_decide":
		return true
	case "/api/policy":
		return r.Method == http.MethodPost
	}
	return false
}

type gateFn func(b map[string]any) (desc string, target types.JID, need bool)

var gatedPaths = map[string]gateFn{}

func notOwn(j types.JID) bool { return !isOwnTarget(j) }

func jidOf(b map[string]any, k string) types.JID {
	s := str(b, k)
	if s != "" && !strings.Contains(s, "@") {
		s += "@s.whatsapp.net"
	}
	j, _ := types.ParseJID(s)
	return j
}

func nameOf(j types.JID) string {
	if j == types.StatusBroadcastJID {
		return "סטטוס"
	}
	name := j.User
	if globalStore != nil {
		var n string
		globalStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", j.String()).Scan(&n)
		if n != "" {
			name = n
		}
	}
	return name
}

func msgPreview(chat types.JID, id string) string {
	var c string
	if globalStore != nil {
		globalStore.db.QueryRow("SELECT coalesce(content,'') FROM messages WHERE id = ? AND chat_jid = ?", id, chat.String()).Scan(&c)
	}
	if r := []rune(c); len(r) > 80 {
		c = string(r[:80]) + "…"
	}
	if c == "" {
		return id
	}
	return "\"" + c + "\""
}

// registerGates lists every outward action that is not a plain send (plain sends, polls, contacts and forwards are
// already drafted by checkPolicy). need=false → runs directly (e.g. the action targets the owner's own chats).
func registerGates() {
	gatedPaths["/api/post_status"] = func(b map[string]any) (string, types.JID, bool) {
		return "📢 פרסום סטטוס (כל אנשי הקשר יראו):\n" + str(b, "text"), types.StatusBroadcastJID, true
	}
	gatedPaths["/api/set_about"] = func(b map[string]any) (string, types.JID, bool) {
		return "👤 שינוי \"אודות\" בפרופיל:\n" + str(b, "text"), types.StatusBroadcastJID, true
	}
	gatedPaths["/api/edit_message"] = func(b map[string]any) (string, types.JID, bool) {
		c := jidOf(b, "jid")
		return fmt.Sprintf("✏️ עריכת הודעה ב%s (%s) לטקסט:\n%s", nameOf(c), msgPreview(c, str(b, "id")), str(b, "text")), c, notOwn(c)
	}
	gatedPaths["/api/delete_message"] = func(b map[string]any) (string, types.JID, bool) {
		c := jidOf(b, "jid")
		return fmt.Sprintf("🗑️ מחיקת הודעה לכולם ב%s: %s", nameOf(c), msgPreview(c, str(b, "id"))), c, notOwn(c)
	}
	gatedPaths["/api/pin_message"] = func(b map[string]any) (string, types.JID, bool) {
		c := jidOf(b, "jid")
		act := "הצמדת"
		if p, ok := b["pin"].(bool); ok && !p {
			act = "ביטול הצמדת"
		}
		return fmt.Sprintf("📌 %s הודעה ב%s: %s", act, nameOf(c), msgPreview(c, str(b, "id"))), c, notOwn(c)
	}
	gatedPaths["/api/react"] = func(b map[string]any) (string, types.JID, bool) {
		c := jidOf(b, "chat_jid")
		return fmt.Sprintf("%s תגובה ב%s על %s", str(b, "emoji"), nameOf(c), msgPreview(c, str(b, "message_id"))), c, notOwn(c)
	}
	gatedPaths["/api/group_participants"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		users, _ := json.Marshal(b["users"])
		heb := map[string]string{"add": "הוספת", "remove": "הסרת", "promote": "מינוי למנהל:", "demote": "הורדת ניהול:"}[str(b, "action")]
		return fmt.Sprintf("👥 %s משתתפים בקבוצה %s: %s", heb, nameOf(g), users), g, notOwn(g) || str(b, "action") == "add"
	}
	gatedPaths["/api/group_photo"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		return "🖼️ החלפת תמונת הקבוצה " + nameOf(g) + " (" + str(b, "path") + ")", g, notOwn(g)
	}
	gatedPaths["/api/group_settings"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		s, _ := json.Marshal(b)
		return "⚙️ שינוי הגדרות הקבוצה " + nameOf(g) + ": " + string(s), g, notOwn(g)
	}
	gatedPaths["/api/set_group"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		d := "✏️ שינוי בקבוצה " + nameOf(g) + ":"
		if n := str(b, "name"); n != "" {
			d += "\nשם: " + n
		}
		if _, ok := b["description"]; ok {
			d += "\nתיאור:\n" + str(b, "description")
		}
		return d, g, notOwn(g)
	}
	gatedPaths["/api/group_invite"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		reset, _ := b["reset"].(bool)
		return "🔗 איפוס קישור ההזמנה של " + nameOf(g) + " (הקישור הישן יפסיק לעבוד)", g, reset && notOwn(g)
	}
	gatedPaths["/api/join_group"] = func(b map[string]any) (string, types.JID, bool) {
		return "➕ הצטרפות לקבוצה: " + str(b, "link"), types.StatusBroadcastJID, true
	}
	gatedPaths["/api/leave_group"] = func(b map[string]any) (string, types.JID, bool) {
		g := jidOf(b, "jid")
		return "🚪 יציאה מהקבוצה " + nameOf(g), g, true
	}
	gatedPaths["/api/create_group"] = func(b map[string]any) (string, types.JID, bool) {
		p, _ := b["participants"].([]any)
		ps, _ := json.Marshal(p)
		return fmt.Sprintf("🆕 יצירת קבוצה \"%s\" עם %s", str(b, "name"), ps), types.StatusBroadcastJID, len(p) > 0
	}
	gatedPaths["/api/block"] = func(b map[string]any) (string, types.JID, bool) {
		j := jidOf(b, "jid")
		act := "חסימת"
		if str(b, "action") == "unblock" {
			act = "ביטול חסימת"
		}
		return act + " " + nameOf(j), j, true
	}
}

// gateRequest returns true when it fully answered the request (drafted it or refused it).
func gateRequest(w http.ResponseWriter, r *http.Request, owner bool) bool {
	fn, ok := gatedPaths[r.URL.Path]
	if !ok || owner || r.Method != http.MethodPost {
		return false
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "bad body", http.StatusBadRequest)
		return true
	}
	r.Body = io.NopCloser(bytes.NewReader(raw))
	var b map[string]any
	if json.Unmarshal(raw, &b) != nil {
		return false // let the handler reject it
	}
	desc, target, need := fn(b)
	if !need {
		return false
	}
	policyMu.Lock()
	p := loadPolicy()
	policyMu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	if t, e := time.Parse(time.RFC3339, p.PausedUntil); e == nil && time.Now().Before(t) {
		json.NewEncoder(w).Encode(map[string]any{"success": false, "error": "paused: " + p.PauseReason})
		return true
	}
	if err := textOK(desc); err != nil {
		json.NewEncoder(w).Encode(map[string]any{"success": false, "error": err.Error()})
		return true
	}
	payload, _ := json.Marshal(map[string]string{"path": r.URL.Path, "body": string(raw)})
	_, info := makeDraftKind(globalClient, p, target, desc, "", "", "api", string(payload))
	json.NewEncoder(w).Encode(map[string]any{"success": true, "drafted": true, "draft": info,
		"message": "DRAFT: not done — an approval card was posted to the control group (owner reacts ✅)"})
	return true
}

// sendApprovedAPI replays an approved gated request (one approval = one action). A pause still blocks it.
func sendApprovedAPI(client *whatsmeow.Client, payload string) (bool, string) {
	var pl struct{ Path, Body string }
	if json.Unmarshal([]byte(payload), &pl) != nil || gatedPaths[pl.Path] == nil {
		return false, "bad draft payload"
	}
	policyMu.Lock()
	p := loadPolicy()
	policyMu.Unlock()
	if t, e := time.Parse(time.RFC3339, p.PausedUntil); e == nil && time.Now().Before(t) {
		return false, "paused: " + p.PauseReason
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	req := httptest.NewRequest(http.MethodPost, pl.Path, strings.NewReader(pl.Body)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	http.DefaultServeMux.ServeHTTP(rec, req)
	out := strings.TrimSpace(rec.Body.String())
	var res map[string]any
	if rec.Code != http.StatusOK {
		return false, fmt.Sprintf("%d %s", rec.Code, out)
	}
	if json.Unmarshal([]byte(out), &res) == nil {
		if s, ok := res["success"].(bool); ok && !s {
			return false, fmt.Sprint(res["error"])
		}
	}
	return true, "done"
}

// apiCardText: the approval card for a gated action.
func apiCardText(draftID int64, desc string) string {
	return fmt.Sprintf("🤖 ⚙️ *פעולה ממתינה #%d*\n👆 לחיצה ארוכה ← ✅ לביצוע (פעם אחת) · ❌ לביטול\n━━━━━━━━━━\n%s\n━━━━━━━━━━", draftID, desc)
}
