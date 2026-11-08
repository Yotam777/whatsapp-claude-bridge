package main

// Message types beyond text/media: a readable line for each (stored + passed to the agent), decryption of RSVP
// answers to events, and building events / @mentions / locations for sending.

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"go.mau.fi/whatsmeow/util/gcmutil"
	"go.mau.fi/whatsmeow/util/hkdfutil"
	"google.golang.org/protobuf/proto"
)

func mapsLink(lat, lng float64) string {
	return fmt.Sprintf("https://maps.google.com/?q=%.6f,%.6f", lat, lng)
}

var vcardTel = regexp.MustCompile(`(?m)^TEL[^:]*:(.+)$`)

func joinNonEmpty(sep string, parts ...string) string {
	out := []string{}
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return strings.Join(out, sep)
}

func nativeFlowLabels(btns []*waE2E.InteractiveMessage_NativeFlowMessage_NativeFlowButton) []string {
	var out []string
	for _, b := range btns {
		var p struct {
			DisplayText string `json:"display_text"`
		}
		json.Unmarshal([]byte(b.GetButtonParamsJSON()), &p)
		if p.DisplayText != "" {
			out = append(out, p.DisplayText)
		}
	}
	return out
}

func bracketed(labels []string) string {
	out := make([]string, len(labels))
	for i, l := range labels {
		out[i] = "[" + l + "]"
	}
	return strings.Join(out, " ")
}

// describeRich: one readable line for a message type the plain text/media path does not cover ("" = not handled).
func describeRich(m *waE2E.Message) string {
	if m == nil {
		return ""
	}
	for _, inner := range []*waE2E.FutureProofMessage{m.GetViewOnceMessage(), m.GetViewOnceMessageV2(), m.GetViewOnceMessageV2Extension()} {
		if inner.GetMessage() != nil {
			if d := joinNonEmpty(" ", extractTextContent(inner.GetMessage())); d != "" {
				return "👁 " + d
			}
			return "👁 הודעת צפייה חד-פעמית"
		}
	}
	if d := m.GetDocumentWithCaptionMessage().GetMessage(); d != nil {
		return d.GetDocumentMessage().GetCaption()
	}
	switch {
	case m.GetLocationMessage() != nil:
		l := m.GetLocationMessage()
		return "📍 מיקום: " + joinNonEmpty(" · ", l.GetName(), l.GetAddress(), mapsLink(l.GetDegreesLatitude(), l.GetDegreesLongitude()))
	case m.GetLiveLocationMessage() != nil:
		l := m.GetLiveLocationMessage()
		return "📍 מיקום חי: " + joinNonEmpty(" · ", l.GetCaption(), mapsLink(l.GetDegreesLatitude(), l.GetDegreesLongitude()))
	case m.GetContactMessage() != nil:
		c := m.GetContactMessage()
		tels := []string{}
		for _, t := range vcardTel.FindAllStringSubmatch(c.GetVcard(), -1) {
			tels = append(tels, strings.TrimSpace(t[1]))
		}
		return "👤 איש קשר: " + joinNonEmpty(" · ", c.GetDisplayName(), strings.Join(tels, ", "))
	case m.GetContactsArrayMessage() != nil:
		names := []string{}
		for _, c := range m.GetContactsArrayMessage().GetContacts() {
			names = append(names, c.GetDisplayName())
		}
		return "👥 אנשי קשר: " + strings.Join(names, ", ")
	case m.GetStickerMessage() != nil:
		return joinNonEmpty(" ", "🏷 מדבקה", m.GetStickerMessage().GetEmojis())
	case m.GetEventMessage() != nil:
		e := m.GetEventMessage()
		when := ""
		if e.GetStartTime() > 0 {
			when = time.Unix(e.GetStartTime(), 0).Local().Format("02/01 15:04")
		}
		pre := "📅 אירוע: "
		if e.GetIsCanceled() {
			pre = "📅 אירוע בוטל: "
		}
		return pre + joinNonEmpty(" · ", e.GetName(), when, e.GetLocation().GetName(), e.GetDescription())
	case m.GetScheduledCallCreationMessage() != nil:
		s := m.GetScheduledCallCreationMessage()
		return "📞 שיחה מתוזמנת: " + joinNonEmpty(" · ", s.GetTitle(), time.UnixMilli(s.GetScheduledTimestampMS()).Local().Format("02/01 15:04"))
	case m.GetButtonsResponseMessage() != nil:
		return "🔘 נבחר: " + m.GetButtonsResponseMessage().GetSelectedDisplayText()
	case m.GetTemplateButtonReplyMessage() != nil:
		return "🔘 נבחר: " + m.GetTemplateButtonReplyMessage().GetSelectedDisplayText()
	case m.GetListResponseMessage() != nil:
		r := m.GetListResponseMessage()
		return "📋 נבחר: " + joinNonEmpty(" · ", r.GetTitle(), r.GetSingleSelectReply().GetSelectedRowID())
	case m.GetInteractiveResponseMessage() != nil:
		return "🔘 נבחר: " + m.GetInteractiveResponseMessage().GetBody().GetText()
	case m.GetButtonsMessage() != nil:
		b := m.GetButtonsMessage()
		labels := []string{}
		for _, x := range b.GetButtons() {
			labels = append(labels, x.GetButtonText().GetDisplayText())
		}
		return joinNonEmpty("\n", b.GetContentText(), bracketed(labels), b.GetFooterText())
	case m.GetInteractiveMessage() != nil:
		i := m.GetInteractiveMessage()
		return joinNonEmpty("\n", i.GetHeader().GetTitle(), i.GetBody().GetText(),
			bracketed(nativeFlowLabels(i.GetNativeFlowMessage().GetButtons())), i.GetFooter().GetText())
	case m.GetListMessage() != nil:
		l := m.GetListMessage()
		return "📋 " + joinNonEmpty("\n", l.GetTitle(), l.GetDescription(), l.GetFooterText())
	case m.GetTemplateMessage() != nil:
		t := m.GetTemplateMessage().GetHydratedTemplate()
		return joinNonEmpty("\n", t.GetHydratedTitleText(), t.GetHydratedContentText(), t.GetHydratedFooterText())
	case m.GetGroupInviteMessage() != nil:
		g := m.GetGroupInviteMessage()
		return "📨 הזמנה לקבוצה: " + joinNonEmpty(" · ", g.GetGroupName(), g.GetCaption())
	}
	return ""
}

// eventResponseText decrypts an RSVP answer to an event (same scheme as poll votes; whatsmeow has no public helper).
func eventResponseText(client *whatsmeow.Client, store *MessageStore, msg *events.Message) string {
	enc := msg.Message.GetEncEventResponseMessage()
	if enc == nil || client == nil {
		return ""
	}
	const failed = "📅 תשובה לאירוע (לא פוענחה)"
	key := enc.GetEventCreationMessageKey()
	origSender, err := origSenderOf(msg, key)
	if err != nil {
		return failed
	}
	secret, stored, err := client.Store.MsgSecrets.GetMessageSecret(context.Background(), msg.Info.Chat, origSender, key.GetID())
	if err != nil || secret == nil {
		return failed
	}
	var plain []byte
	err = errString("no sender")
	for _, s := range []types.JID{origSender, stored} {
		if s.IsEmpty() {
			continue
		}
		k, ad := eventSecretKey(msg.Info.Sender, key.GetID(), s, secret)
		if plain, err = gcmutil.Decrypt(k, enc.GetEncIV(), enc.GetEncPayload(), ad); err == nil {
			break
		}
	}
	var r waE2E.EventResponseMessage
	if err != nil || proto.Unmarshal(plain, &r) != nil {
		return failed
	}
	return eventAnswerLine(&r, eventName(store, key.GetID()))
}

func eventAnswerLine(r *waE2E.EventResponseMessage, name string) string {
	ans := map[waE2E.EventResponseMessage_EventResponseType]string{
		waE2E.EventResponseMessage_GOING: "✅ מגיע", waE2E.EventResponseMessage_NOT_GOING: "❌ לא מגיע",
		waE2E.EventResponseMessage_MAYBE: "🤔 אולי"}[r.GetResponse()]
	if ans == "" {
		ans = "?"
	}
	if r.GetExtraGuestCount() > 0 {
		ans += fmt.Sprintf(" (+%d)", r.GetExtraGuestCount())
	}
	return joinNonEmpty(" · ", "📅 תשובה לאירוע: "+ans, name)
}

func eventName(store *MessageStore, id string) string {
	if store == nil {
		return ""
	}
	var c string
	store.db.QueryRow("SELECT content FROM messages WHERE id = ?", id).Scan(&c)
	return strings.TrimPrefix(strings.SplitN(c, " · ", 2)[0], "📅 אירוע: ")
}

func eventSecretKey(responder types.JID, origID string, origSender types.JID, secret []byte) ([]byte, []byte) {
	o, r := origSender.ToNonAD().String(), responder.ToNonAD().String()
	return hkdfutil.SHA256(secret, nil, []byte(origID+o+r+"Event Response"), 32), []byte(origID + "\x00" + r)
}

func origSenderOf(msg *events.Message, key *waCommon.MessageKey) (types.JID, error) {
	if key.GetFromMe() {
		if msg.Info.IsFromMe || globalClient == nil || globalClient.Store.ID == nil {
			return msg.Info.Sender, nil
		}
		if msg.Info.Sender.Server == types.HiddenUserServer && !globalClient.Store.LID.IsEmpty() {
			return globalClient.Store.LID.ToNonAD(), nil
		}
		return globalClient.Store.ID.ToNonAD(), nil
	}
	if msg.Info.Chat.Server == types.DefaultUserServer || msg.Info.Chat.Server == types.HiddenUserServer {
		return types.ParseJID(key.GetRemoteJID())
	}
	return types.ParseJID(key.GetParticipant())
}

// ---- sending ----

var mentionRe = regexp.MustCompile(`@(\d{8,15})`)

func buildRich(kind string, sp specialPayload) (*waE2E.Message, string, error) {
	switch kind {
	case "event":
		if sp.Name == "" || sp.Start <= 0 {
			return nil, "", errString("event needs name and start (unix seconds)")
		}
		e := &waE2E.EventMessage{Name: proto.String(sp.Name), StartTime: proto.Int64(sp.Start), IsCanceled: proto.Bool(false),
			ExtraGuestsAllowed: proto.Bool(sp.Guests)}
		if sp.Text != "" {
			e.Description = proto.String(sp.Text)
		}
		if sp.End > sp.Start {
			e.EndTime = proto.Int64(sp.End)
		}
		if sp.Address != "" || sp.Lat != 0 || sp.Lng != 0 {
			e.Location = &waE2E.LocationMessage{Name: proto.String(sp.Address), DegreesLatitude: proto.Float64(sp.Lat),
				DegreesLongitude: proto.Float64(sp.Lng)}
		}
		desc := describeRich(&waE2E.Message{EventMessage: e})
		return &waE2E.Message{EventMessage: e, MessageContextInfo: &waE2E.MessageContextInfo{MessageSecret: secret32()}}, desc, nil
	case "mention":
		text := sp.Text
		jids := []string{}
		seen := map[string]bool{}
		add := func(p string) {
			p = strings.TrimLeft(strings.TrimSpace(p), "+@")
			if p != "" && !seen[p] {
				seen[p] = true
				jids = append(jids, p+"@s.whatsapp.net")
				if !strings.Contains(text, "@"+p) {
					text = "@" + p + " " + text
				}
			}
		}
		for _, p := range sp.Mentions {
			add(p)
		}
		for _, m := range mentionRe.FindAllStringSubmatch(sp.Text, -1) {
			add(m[1])
		}
		if strings.TrimSpace(sp.Text) == "" || len(jids) == 0 {
			return nil, "", errString("mention needs text and at least one phone")
		}
		return &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(text),
			ContextInfo: &waE2E.ContextInfo{MentionedJID: jids}}}, text, nil
	case "location":
		if sp.Lat == 0 && sp.Lng == 0 {
			return nil, "", errString("location needs lat and lng")
		}
		l := &waE2E.LocationMessage{DegreesLatitude: proto.Float64(sp.Lat), DegreesLongitude: proto.Float64(sp.Lng)}
		if sp.Name != "" {
			l.Name = proto.String(sp.Name)
		}
		if sp.Address != "" {
			l.Address = proto.String(sp.Address)
		}
		return &waE2E.Message{LocationMessage: l}, describeRich(&waE2E.Message{LocationMessage: l}), nil
	}
	return nil, "", errString("unknown kind " + kind)
}
