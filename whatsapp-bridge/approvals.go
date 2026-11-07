package main

// Draft approval by reaction: the owner reacts ✅ on a draft card in the control group and exactly that one draft
// is sent (one approval = one message to one recipient); ❌ cancels it. Anything else is ignored.

import (
	"context"
	"fmt"
	"sync"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
)

// oneShotApproval holds recipients (JID strings) allowed exactly one direct send; checkPolicy consumes the entry.
var oneShotApproval sync.Map

// handleReaction returns true if the event was a reaction (handled or not).
func handleReaction(client *whatsmeow.Client, msg *events.Message) bool {
	r := msg.Message.GetReactionMessage()
	if r == nil {
		return false
	}
	if !msg.Info.IsFromMe || globalStore == nil { // only the owner's own reactions count
		return true
	}
	policyMu.Lock()
	control := loadPolicy().ControlGroup
	policyMu.Unlock()
	if msg.Info.Chat.String() != control {
		return true
	}
	emoji := r.GetText()
	if emoji != "✅" && emoji != "✔️" && emoji != "👍" && emoji != "❌" {
		return true
	}
	cardID := r.GetKey().GetID()
	var id int64
	if err := globalStore.db.QueryRow(`SELECT id FROM drafts WHERE card_id = ? AND status = 'pending'`, cardID).Scan(&id); err != nil {
		return true // not a pending draft card
	}
	decideDraft(client, id, emoji != "❌", control)
	return true
}

// decideDraft sends (approve) or cancels exactly one pending draft: one approval = one message to one recipient.
func decideDraft(client *whatsmeow.Client, id int64, approve bool, control string) (string, error) {
	var recipient, name, message, media, quoted string
	err := globalStore.db.QueryRow(`SELECT recipient, name, message, COALESCE(media_path,''), COALESCE(quoted_id,'')
		FROM drafts WHERE id = ? AND status = 'pending'`, id).Scan(&recipient, &name, &message, &media, &quoted)
	if err != nil {
		return "", fmt.Errorf("draft %d is not pending", id)
	}
	if !approve {
		globalStore.db.Exec("UPDATE drafts SET status = 'cancelled' WHERE id = ? AND status = 'pending'", id)
		go sendWhatsAppMessage(client, control, "🤖 _הטיוטה ל"+name+" בוטלה._", "")
		return "cancelled", nil
	}
	// claim the draft atomically so a repeated approval can never send twice
	res, err := globalStore.db.Exec("UPDATE drafts SET status = 'sending' WHERE id = ? AND status = 'pending'", id)
	if n, _ := res.RowsAffected(); err != nil || n != 1 {
		return "", fmt.Errorf("draft %d already handled", id)
	}
	go func() {
		oneShotApproval.Store(recipient, true)
		ok, detail := sendWhatsAppMessageQuoted(client, recipient, message, media, quoted)
		oneShotApproval.Delete(recipient) // never leave an approval behind
		status, note := "sent", fmt.Sprintf("🤖 ✅ _נשלח ל%s (אישור אחד = הודעה אחת)._", name)
		if !ok {
			status, note = "failed", fmt.Sprintf("🤖 ⚠️ _השליחה ל%s נכשלה: %s_", name, detail)
		}
		globalStore.db.Exec("UPDATE drafts SET status = ? WHERE id = ?", status, id)
		sendWhatsAppMessage(client, control, note, "")
	}()
	return "sending", nil
}

// registerApprovalAPI: the dashboard's approve / cancel buttons ({"id":12,"approve":true}); same one-shot rules as the reaction.
func registerApprovalAPI(client *whatsmeow.Client) {
	adminHandler("/api/draft_decide", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		id, _ := b["id"].(float64)
		ok, _ := b["approve"].(bool)
		policyMu.Lock()
		control := loadPolicy().ControlGroup
		policyMu.Unlock()
		st, err := decideDraft(client, int64(id), ok, control)
		return map[string]any{"status": st}, err
	})
}
