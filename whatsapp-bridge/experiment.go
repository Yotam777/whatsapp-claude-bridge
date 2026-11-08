package main

// /api/experiment: sends one test message of a WhatsApp type the tool does not use yet, ONLY to the owner's own
// chats / control groups, to find out which types this (non-business) account can actually send and how they show.

import (
	"context"
	"crypto/rand"
	"fmt"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

func secret32() []byte {
	b := make([]byte, 32)
	rand.Read(b)
	return b
}

func buildExperiment(client *whatsmeow.Client, kind string) (*waE2E.Message, error) {
	tomorrow := time.Now().Add(24 * time.Hour).Truncate(time.Hour)
	own := client.Store.ID.ToNonAD()
	switch kind {
	case "event":
		return &waE2E.Message{EventMessage: &waE2E.EventMessage{
			Name: proto.String("ניסוי: אירוע עם אישור הגעה"), Description: proto.String("בדיקה של הגשר. אפשר להתעלם."),
			StartTime: proto.Int64(tomorrow.Unix()), EndTime: proto.Int64(tomorrow.Add(time.Hour).Unix()),
			Location:  &waE2E.LocationMessage{Name: proto.String("מחולה"), DegreesLatitude: proto.Float64(32.36), DegreesLongitude: proto.Float64(35.51)},
			ExtraGuestsAllowed: proto.Bool(false), IsCanceled: proto.Bool(false),
		}, MessageContextInfo: &waE2E.MessageContextInfo{MessageSecret: secret32()}}, nil
	case "mention":
		return &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text:        proto.String("ניסוי תיוג: @" + own.User + " (אמורה להגיע התראה גם בקבוצה מושתקת)"),
			ContextInfo: &waE2E.ContextInfo{MentionedJID: []string{own.String()}},
		}}, nil
	case "location":
		return &waE2E.Message{LocationMessage: &waE2E.LocationMessage{
			DegreesLatitude: proto.Float64(32.36), DegreesLongitude: proto.Float64(35.51),
			Name: proto.String("ניסוי מיקום: נקודת עבודה"), Address: proto.String("מחולה, בקעת הירדן"),
		}}, nil
	case "scheduled_call":
		return &waE2E.Message{ScheduledCallCreationMessage: &waE2E.ScheduledCallCreationMessage{
			ScheduledTimestampMS: proto.Int64(tomorrow.UnixMilli()), CallType: waE2E.ScheduledCallCreationMessage_VOICE.Enum(),
			Title: proto.String("ניסוי: שיחה מתוזמנת"),
		}}, nil
	case "buttons":
		btn := func(id, t string) *waE2E.ButtonsMessage_Button {
			return &waE2E.ButtonsMessage_Button{ButtonID: proto.String(id), ButtonText: &waE2E.ButtonsMessage_Button_ButtonText{DisplayText: proto.String(t)},
				Type: waE2E.ButtonsMessage_Button_RESPONSE.Enum()}
		}
		return &waE2E.Message{ButtonsMessage: &waE2E.ButtonsMessage{
			ContentText: proto.String("ניסוי כפתורים + footer: מגיע מחר?"), FooterText: proto.String("ניסוי footer: אישורי הגעה"),
			HeaderType: waE2E.ButtonsMessage_EMPTY.Enum(), Buttons: []*waE2E.ButtonsMessage_Button{btn("y", "כן"), btn("n", "לא"), btn("m", "עוד לא יודע")},
		}}, nil
	case "list":
		return &waE2E.Message{ListMessage: &waE2E.ListMessage{
			Title: proto.String("ניסוי רשימה"), Description: proto.String("בחר סטטוס"), ButtonText: proto.String("פתח"),
			ListType: waE2E.ListMessage_SINGLE_SELECT.Enum(), FooterText: proto.String("ניסוי footer ברשימה"),
			Sections: []*waE2E.ListMessage_Section{{Title: proto.String("סטטוס"), Rows: []*waE2E.ListMessage_Row{
				{RowID: proto.String("1"), Title: proto.String("טופל")}, {RowID: proto.String("2"), Title: proto.String("בטיפול")}}}},
		}}, nil
	case "footer":
		return &waE2E.Message{ViewOnceMessage: &waE2E.FutureProofMessage{Message: &waE2E.Message{
			InteractiveMessage: &waE2E.InteractiveMessage{
				Body:   &waE2E.InteractiveMessage_Body{Text: proto.String("ניסוי footer בלבד (בלי כפתורים): תודה, תשובתך נרשמה 🙏")},
				Footer: &waE2E.InteractiveMessage_Footer{Text: proto.String("לשינוי: השב עם מספר המגיעים")},
			},
			MessageContextInfo: &waE2E.MessageContextInfo{DeviceListMetadataVersion: proto.Int32(2)},
		}}}, nil
	case "interactive":
		qr := func(id, t string) *waE2E.InteractiveMessage_NativeFlowMessage_NativeFlowButton {
			return &waE2E.InteractiveMessage_NativeFlowMessage_NativeFlowButton{Name: proto.String("quick_reply"),
				ButtonParamsJSON: proto.String(fmt.Sprintf(`{"display_text":%q,"id":%q}`, t, id))}
		}
		inner := &waE2E.Message{
			InteractiveMessage: &waE2E.InteractiveMessage{
				Body:   &waE2E.InteractiveMessage_Body{Text: proto.String("ניסוי הודעה אינטראקטיבית: מגיע מחר?")},
				Footer: &waE2E.InteractiveMessage_Footer{Text: proto.String("ניסוי footer: לשינוי השב עם מספר")},
				InteractiveMessage: &waE2E.InteractiveMessage_NativeFlowMessage_{NativeFlowMessage: &waE2E.InteractiveMessage_NativeFlowMessage{
					Buttons: []*waE2E.InteractiveMessage_NativeFlowMessage_NativeFlowButton{qr("y", "כן, אגיע"), qr("n", "לא אגיע")}}},
			},
			MessageContextInfo: &waE2E.MessageContextInfo{DeviceListMetadataVersion: proto.Int32(2)},
		}
		return &waE2E.Message{ViewOnceMessage: &waE2E.FutureProofMessage{Message: inner}}, nil
	}
	return nil, errString("unknown kind " + kind)
}

func registerExperimentAPI(client *whatsmeow.Client) {
	// {"jid","kind"} — kind: event | mention | location | scheduled_call | buttons | list | interactive
	adminHandler("/api/experiment", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		to, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		if !ownIn(loadPolicy(), to) {
			return nil, errString("experiments go only to the owner's own chats / control groups")
		}
		msg, err := buildExperiment(client, str(b, "kind"))
		if err != nil {
			return nil, err
		}
		resp, err := client.SendMessage(ctx, to, msg)
		if err == nil {
			recordSent(to, resp.ID, "experiment "+str(b, "kind"), "", "", true, "experiment")
		}
		return map[string]any{"id": resp.ID}, err
	})
}
