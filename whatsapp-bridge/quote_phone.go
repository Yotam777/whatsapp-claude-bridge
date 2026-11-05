package main

// Quoted replies and phone drafts.

import (
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"strings"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// quoteContext builds the ContextInfo that makes a text message a reply to quotedID in the same chat.
func quoteContext(chat types.JID, quotedID string) *waProto.ContextInfo {
	if quotedID == "" || globalStore == nil {
		return nil
	}
	var sender, content string
	var fromMe bool
	err := globalStore.db.QueryRow("SELECT sender, content, is_from_me FROM messages WHERE id = ? AND chat_jid = ?",
		quotedID, chat.String()).Scan(&sender, &content, &fromMe)
	if err != nil {
		return nil
	}
	participant := chat
	if chat.Server == types.GroupServer || fromMe {
		server := types.DefaultUserServer
		if !strings.HasPrefix(sender, "972") && len(sender) >= 14 {
			server = types.HiddenUserServer // LID-addressed participant
		}
		participant = types.NewJID(sender, server)
	}
	return &waProto.ContextInfo{
		StanzaID:      proto.String(quotedID),
		Participant:   proto.String(participant.String()),
		QuotedMessage: &waProto.Message{Conversation: proto.String(content)},
	}
}

// phoneDraft (optional, off unless WA_PHONE_DRAFTS=1) opens the chat on the owner's Android phone over ADB with the
// text already typed and unsent; WhatsApp keeps unsent text as the chat's draft. Needs `adb` and a paired phone.
func phoneDraft(phone, message string) error {
	if os.Getenv("WA_PHONE_DRAFTS") != "1" {
		return fmt.Errorf("phone drafts disabled")
	}
	adb := os.Getenv("WA_ADB")
	if adb == "" {
		var err error
		if adb, err = exec.LookPath("adb"); err != nil {
			return fmt.Errorf("adb not found")
		}
	}
	out, err := exec.Command(adb, "devices").Output()
	if err != nil || !strings.Contains(strings.TrimPrefix(string(out), "List of devices attached"), "\tdevice") {
		return fmt.Errorf("no phone connected over adb")
	}
	link := "https://wa.me/" + phone + "?text=" + url.QueryEscape(message)
	cmd := fmt.Sprintf("am start -a android.intent.action.VIEW -d '%s' com.whatsapp", link)
	res, err := exec.Command(adb, "shell", cmd).CombinedOutput()
	if err != nil || strings.Contains(string(res), "Error") {
		return fmt.Errorf("adb start failed: %v %s", err, res)
	}
	return nil
}
