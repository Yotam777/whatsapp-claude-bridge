package main

// Sync-engine extras:
//   - /api/events: Server-Sent Events push of every stored message / edit / delete (agent, dashboard, other apps)
//   - edits and deletions update the stored message
//   - unique media file names (message id), so files never overwrite each other
//   - private chats addressed by LID are stored under the phone-number JID (one conversation per person)
//   - /api/react and /api/typing
//   - bearer-token protection of the whole REST API (token in store/api_token)

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

var globalClient *whatsmeow.Client

// ---------- event stream ----------

var (
	subsMu sync.Mutex
	subs   = map[chan []byte]struct{}{}
)

func publish(evt map[string]any) {
	b, err := json.Marshal(evt)
	if err != nil {
		return
	}
	subsMu.Lock()
	defer subsMu.Unlock()
	for ch := range subs {
		select {
		case ch <- b:
		default: // a slow listener must never block message handling
		}
	}
}

func eventsHandler(w http.ResponseWriter, r *http.Request) {
	fl, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	ch := make(chan []byte, 256)
	subsMu.Lock()
	subs[ch] = struct{}{}
	subsMu.Unlock()
	defer func() {
		subsMu.Lock()
		delete(subs, ch)
		subsMu.Unlock()
	}()
	fmt.Fprint(w, ": connected\n\n")
	fl.Flush()
	ping := time.NewTicker(25 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case b := <-ch:
			fmt.Fprintf(w, "data: %s\n\n", b)
			fl.Flush()
		case <-ping.C:
			fmt.Fprint(w, ": ping\n\n")
			fl.Flush()
		}
	}
}

func publishMessage(id, chat, sender, content, mediaType string, ts time.Time, fromMe bool) {
	publish(map[string]any{"type": "message", "id": id, "chat_jid": chat, "sender": sender, "content": content,
		"media_type": mediaType, "timestamp": ts.Format(time.RFC3339), "is_from_me": fromMe})
}

// ---------- identity ----------

// normalizeChat maps a LID-addressed private chat to its phone-number JID when the mapping is known.
func normalizeChat(client *whatsmeow.Client, jid types.JID) types.JID {
	if jid.Server != types.HiddenUserServer || client == nil || client.Store == nil || client.Store.LIDs == nil {
		return jid
	}
	if pn, err := client.Store.LIDs.GetPNForLID(context.Background(), jid.ToNonAD()); err == nil && !pn.IsEmpty() {
		return pn.ToNonAD()
	}
	return jid
}

func normalizeUser(client *whatsmeow.Client, user string) string {
	if strings.HasPrefix(user, "972") || len(user) < 14 {
		return user
	}
	return normalizeChat(client, types.NewJID(user, types.HiddenUserServer)).User
}

// mediaFilename gives every media file a name that cannot collide: <type>_<message id>.<ext> (documents keep their name).
func mediaFilename(id, mediaType, original string) string {
	if id == "" {
		return original
	}
	if mediaType == "document" && original != "" && !strings.HasPrefix(original, "document_") {
		return id + "_" + filepath.Base(original)
	}
	return mediaType + "_" + id + filepath.Ext(original)
}

// ---------- edits and deletions ----------

// handleProtocol applies edits/revokes to stored messages; returns true if the event was such a protocol message.
func handleProtocol(client *whatsmeow.Client, store *MessageStore, msg *events.Message) bool {
	pm := msg.Message.GetProtocolMessage()
	if pm == nil {
		return false
	}
	chat := normalizeChat(client, msg.Info.Chat).String()
	target := pm.GetKey().GetID()
	switch pm.GetType() {
	case waProto.ProtocolMessage_REVOKE:
		store.db.Exec("UPDATE messages SET content = '🗑️ (ההודעה נמחקה)', deleted = 1 WHERE id = ? AND chat_jid = ?", target, chat)
		publish(map[string]any{"type": "deleted", "id": target, "chat_jid": chat})
		return true
	case waProto.ProtocolMessage_MESSAGE_EDIT:
		text := extractTextContent(pm.GetEditedMessage())
		store.db.Exec("UPDATE messages SET content = ?, edited = 1 WHERE id = ? AND chat_jid = ?", text, target, chat)
		publish(map[string]any{"type": "edited", "id": target, "chat_jid": chat, "content": text})
		return true
	}
	return false
}

func addSyncColumns(store *MessageStore) {
	for _, col := range []string{"ALTER TABLE messages ADD COLUMN edited INTEGER DEFAULT 0",
		"ALTER TABLE messages ADD COLUMN deleted INTEGER DEFAULT 0"} {
		store.db.Exec(col) // fails harmlessly when the column already exists
	}
	store.db.Exec("PRAGMA journal_mode=WAL")
	store.db.Exec("PRAGMA busy_timeout=10000")
}

// ---------- auth ----------

var apiToken string

func loadToken() string {
	path := "store/api_token"
	if b, err := os.ReadFile(path); err == nil && len(strings.TrimSpace(string(b))) >= 16 {
		return strings.TrimSpace(string(b))
	}
	buf := make([]byte, 24)
	rand.Read(buf)
	t := hex.EncodeToString(buf)
	os.WriteFile(path, []byte(t), 0600)
	return t
}

// withAuth requires "Authorization: Bearer <token>" (or ?token= for EventSource clients) on every request.
func withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if got == "" {
			got = r.URL.Query().Get("token")
		}
		if apiToken == "" || got != apiToken {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// ---------- reactions / typing ----------

func registerSyncAPI(client *whatsmeow.Client) {
	http.HandleFunc("/api/events", eventsHandler)

	// {"chat_jid":..., "message_id":..., "sender": "<user or jid, empty = me>", "emoji":"👀"}  (empty emoji removes it)
	http.HandleFunc("/api/react", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			ChatJID   string `json:"chat_jid"`
			MessageID string `json:"message_id"`
			Sender    string `json:"sender"`
			Emoji     string `json:"emoji"`
		}
		if json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		chat, err := types.ParseJID(req.ChatJID)
		if err != nil {
			http.Error(w, "bad chat", http.StatusBadRequest)
			return
		}
		sender := client.Store.ID.ToNonAD()
		if req.Sender != "" {
			if strings.Contains(req.Sender, "@") {
				sender, _ = types.ParseJID(req.Sender)
			} else {
				sender = types.NewJID(req.Sender, types.DefaultUserServer)
			}
		}
		_, err = client.SendMessage(context.Background(), chat, client.BuildReaction(chat, sender, req.MessageID, req.Emoji))
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"success": err == nil, "error": fmt.Sprint(err)})
	})

	// {"chat_jid":..., "on": true}
	http.HandleFunc("/api/typing", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			ChatJID string `json:"chat_jid"`
			On      bool   `json:"on"`
		}
		if json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		chat, err := types.ParseJID(req.ChatJID)
		if err == nil {
			state := types.ChatPresencePaused
			if req.On {
				state = types.ChatPresenceComposing
			}
			err = client.SendChatPresence(context.Background(), chat, state, types.ChatPresenceMediaText)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"success": err == nil})
	})
}
