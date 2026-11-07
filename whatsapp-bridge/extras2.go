package main

// Profile / status / pin / star / read / poll / contact / forward endpoints (POST json, localhost + token).
// Anything that reaches OTHER people (poll, contact, forward, status) goes through the send policy
// (draft by default) and the pacer. Nothing here acts on its own: the agent calls it only on Yakir's instruction.

import (
	"context"
	"fmt"
	"path/filepath"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/appstate"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

func parseUserJID(s string) (types.JID, bool) {
	if s == "" {
		return types.EmptyJID, false
	}
	if j, err := types.ParseJID(s); err == nil && j.Server != "" {
		return j, true
	}
	if j, err := types.ParseJID(s + "@s.whatsapp.net"); err == nil {
		return j, true
	}
	return types.EmptyJID, false
}

func registerExtras2API(client *whatsmeow.Client) {
	// resolves chat, author and from_me of a stored message (explicit fields win over the DB)
	msgRef := func(b map[string]any) (chat, sender types.JID, id string, fromMe bool, err error) {
		if chat, err = types.ParseJID(str(b, "jid")); err != nil {
			return
		}
		id = str(b, "id")
		fromMe, _ = b["from_me"].(bool)
		sender = types.EmptyJID
		if j, ok := parseUserJID(str(b, "sender")); ok {
			sender = j
		} else if globalStore != nil {
			var s string
			var fm bool
			if globalStore.db.QueryRow("SELECT sender, is_from_me FROM messages WHERE id=? AND chat_jid=?", id, chat.String()).Scan(&s, &fm) == nil {
				fromMe = fm
				if j, ok := parseUserJID(s); ok && !fm {
					sender = j
				}
			}
		}
		if sender.IsEmpty() {
			sender = chat
			if fromMe && client.Store.ID != nil {
				sender = client.Store.ID.ToNonAD()
			}
		}
		return
	}

	// {"text":"..."} — profile About text
	adminHandler("/api/set_about", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		t := str(b, "text")
		if err := textOK(t); err != nil {
			return nil, err
		}
		return nil, client.SetStatusMessage(ctx, types.SetStatusInput{Text: &t})
	})

	// {"text":"...","approved":true} — post a text status (story). Outward-facing: needs approved=true.
	adminHandler("/api/post_status", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		if ok, _ := b["approved"].(bool); !ok {
			return nil, errString("status posts need approved=true (only after Yakir approved the exact text)")
		}
		t := str(b, "text")
		if err := textOK(t); err != nil || t == "" {
			return nil, errString("bad status text")
		}
		resp, err := client.SendMessage(ctx, types.StatusBroadcastJID, &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text: proto.String(t), BackgroundArgb: proto.Uint32(0xFF1B5E20),
		}})
		return map[string]any{"id": resp.ID}, err
	})

	// {"jid","id","pin":true|false,"seconds":604800}
	adminHandler("/api/pin_message", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, sender, id, fromMe, err := msgRef(b)
		if err != nil {
			return nil, err
		}
		typ := waE2E.PinInChatMessage_PIN_FOR_ALL
		if p, ok := b["pin"].(bool); ok && !p {
			typ = waE2E.PinInChatMessage_UNPIN_FOR_ALL
		}
		key := &waCommon.MessageKey{RemoteJID: proto.String(chat.String()), FromMe: proto.Bool(fromMe), ID: proto.String(id)}
		if !fromMe && chat.Server == types.GroupServer {
			key.Participant = proto.String(sender.String())
		}
		pin := &waE2E.PinInChatMessage{Key: key, Type: typ.Enum(), SenderTimestampMS: proto.Int64(time.Now().UnixMilli())}
		msg := &waE2E.Message{PinInChatMessage: pin}
		if typ == waE2E.PinInChatMessage_PIN_FOR_ALL {
			secs := uint32(604800)
			if s, ok := b["seconds"].(float64); ok {
				secs = uint32(s)
			}
			msg.MessageContextInfo = &waE2E.MessageContextInfo{MessageAddOnDurationInSecs: proto.Uint32(secs)}
		}
		_, err = client.SendMessage(ctx, chat, msg)
		return nil, err
	})

	// {"jid","pin":bool} — pin / unpin a chat in the chat list
	adminHandler("/api/pin_chat", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		j, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		p, _ := b["pin"].(bool)
		return nil, client.SendAppState(ctx, appstate.BuildPin(j, p))
	})

	// {"jid","id","star":bool,"sender"?,"from_me"?}
	adminHandler("/api/star", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, sender, id, fromMe, err := msgRef(b)
		if err != nil {
			return nil, err
		}
		s, _ := b["star"].(bool)
		return nil, client.SendAppState(ctx, appstate.BuildStar(chat, sender, id, fromMe, s))
	})

	// {"jid","unread":bool?} — mark a chat read (read receipts for its latest incoming messages) or unread
	adminHandler("/api/mark_read", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		if un, _ := b["unread"].(bool); un {
			return nil, client.SendAppState(ctx, appstate.BuildMarkChatAsRead(chat, false, time.Now(), nil))
		}
		ids := []types.MessageID{}
		sender := types.EmptyJID
		if globalStore != nil {
			rows, e := globalStore.db.Query("SELECT id, sender FROM messages WHERE chat_jid=? AND is_from_me=0 ORDER BY timestamp DESC LIMIT 20", chat.String())
			if e == nil {
				for rows.Next() {
					var id, s string
					rows.Scan(&id, &s)
					ids = append(ids, id)
					if j, ok := parseUserJID(s); ok && sender.IsEmpty() {
						sender = j
					}
				}
				rows.Close()
			}
		}
		if len(ids) > 0 {
			if sender.IsEmpty() {
				sender = chat
			}
			if err = client.MarkRead(ctx, ids, time.Now(), chat, sender); err != nil {
				return nil, err
			}
		}
		_ = client.SendAppState(ctx, appstate.BuildMarkChatAsRead(chat, true, time.Now(), nil))
		return map[string]any{"marked": len(ids)}, nil
	})

	// policy check + pacing; non-send decisions become a text draft describing the item
	gate := func(to types.JID, describe string) (bool, map[string]any, error) {
		switch d, pol := checkPolicy(to); d {
		case "paused":
			return false, nil, errString("paused: " + pol.PauseReason)
		case "draft":
			_, info := makeDraft(client, pol, to, describe, "", "")
			return false, map[string]any{"draft": info}, nil
		}
		return true, nil, sendPacer.wait(client, to, len(describe))
	}

	// {"jid","question","options":[...],"max":1}
	adminHandler("/api/send_poll", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		to, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		opts := []string{}
		if l, ok := b["options"].([]any); ok {
			for _, o := range l {
				if s, ok := o.(string); ok {
					opts = append(opts, s)
				}
			}
		}
		q := str(b, "question")
		if err := textOK(q); err != nil || q == "" || len(opts) < 2 {
			return nil, errString("need a valid question and >=2 options")
		}
		max := 1
		if m, ok := b["max"].(float64); ok && m >= 1 {
			max = int(m)
		}
		desc := "📊 " + q
		for _, o := range opts {
			desc += "\n▫️ " + o
		}
		ok, res, err := gate(to, desc+"\n(סקר — שלח ידנית כסקר)")
		if !ok {
			return res, err
		}
		resp, err := client.SendMessage(ctx, to, client.BuildPollCreation(q, opts, max))
		if err == nil {
			recordSent(to, resp.ID, desc, "", "", true, "poll")
		}
		return map[string]any{"id": resp.ID}, err
	})

	// {"jid","name","phone":"9725..."} — share a contact card
	adminHandler("/api/send_contact", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		to, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		name, phone := str(b, "name"), str(b, "phone")
		if textOK(name) != nil || name == "" || phone == "" {
			return nil, errString("need name and phone")
		}
		desc := fmt.Sprintf("👤 %s: +%s", name, phone)
		ok, res, err := gate(to, desc)
		if !ok {
			return res, err
		}
		vcard := fmt.Sprintf("BEGIN:VCARD\nVERSION:3.0\nFN:%s\nTEL;type=CELL;waid=%s:+%s\nEND:VCARD", name, phone, phone)
		resp, err := client.SendMessage(ctx, to, &waE2E.Message{ContactMessage: &waE2E.ContactMessage{DisplayName: proto.String(name), Vcard: proto.String(vcard)}})
		if err == nil {
			recordSent(to, resp.ID, desc, "", "", true, "contact")
		}
		return map[string]any{"id": resp.ID}, err
	})

	// {"jid":"target chat","from":"source chat","id":"msg id"} — forward a message (marked as forwarded). Media (image,
	// video, audio, document) is downloaded and re-sent with its caption through the normal send path (policy + pacer).
	adminHandler("/api/forward", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		to, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		var text, mediaType string
		if globalStore == nil || globalStore.db.QueryRow("SELECT coalesce(content,''), coalesce(media_type,'') FROM messages WHERE id=? AND chat_jid=?",
			str(b, "id"), str(b, "from")).Scan(&text, &mediaType) != nil {
			return nil, errString("message not found")
		}
		if mediaType != "" {
			okDl, _, _, path, dlErr := downloadMedia(client, globalStore, str(b, "id"), str(b, "from"))
			if !okDl || dlErr != nil {
				return nil, fmt.Errorf("media download failed: %v", dlErr)
			}
			if abs, e := filepath.Abs(path); e == nil {
				path = abs
			}
			sent, msg := sendWhatsAppMessage(client, to.String(), text, path)
			return map[string]any{"sent": sent, "message": msg, "media_type": mediaType}, nil
		}
		if text == "" {
			return nil, errString("empty message")
		}
		ok, res, err := gate(to, text)
		if !ok {
			return res, err
		}
		resp, err := client.SendMessage(ctx, to, &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text: proto.String(text), ContextInfo: &waE2E.ContextInfo{IsForwarded: proto.Bool(true), ForwardingScore: proto.Uint32(1)},
		}})
		if err == nil {
			recordSent(to, resp.ID, text, "", "", true, "forward")
		}
		return map[string]any{"id": resp.ID}, err
	})
}
