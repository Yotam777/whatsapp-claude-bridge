package main

// Extra REST endpoints for the control panel: health/status and the send policy.

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
)

var bridgeStarted = time.Now()

// version is set at build time: go build -ldflags "-X main.version=1.2.3"
var version = "dev"

// connection bookkeeping for the control panel
var (
	connMu         sync.Mutex
	connectedSince time.Time
	lastDisconnect time.Time
	disconnects    int
)

func logConn(event string) {
	if globalStore != nil {
		globalStore.db.Exec("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
		globalStore.db.Exec("INSERT INTO conn_log (at, event) VALUES (?, ?)", time.Now().Format("2006-01-02 15:04:05"), event)
	}
	publish(map[string]any{"type": "connection", "event": event, "at": time.Now().Format(time.RFC3339)})
}

func markConnected() {
	connMu.Lock()
	connectedSince = time.Now()
	connMu.Unlock()
	logConn("connected")
}

func markDisconnected() {
	connMu.Lock()
	lastDisconnect, connectedSince = time.Now(), time.Time{}
	disconnects++
	connMu.Unlock()
	logConn("disconnected")
}

func registerExtraAPI(client *whatsmeow.Client) {
	http.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		p := loadPolicy()
		var sentToday, draftsPending int
		if globalStore != nil {
			globalStore.db.QueryRow("SELECT COUNT(*) FROM send_log WHERE ok = 1 AND substr(timestamp,1,10) = date('now','localtime')").Scan(&sentToday)
			globalStore.db.QueryRow("SELECT COUNT(*) FROM drafts WHERE status = 'pending'").Scan(&draftsPending)
		}
		paused := false
		if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && time.Now().Before(t) {
			paused = true
		}
		connMu.Lock()
		since, lastDisc, discs := connectedSince, lastDisconnect, disconnects
		connMu.Unlock()
		fmtT := func(t time.Time) string {
			if t.IsZero() {
				return ""
			}
			return t.Format(time.RFC3339)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"version":         version,
			"connected_since": fmtT(since),
			"last_disconnect": fmtT(lastDisc),
			"disconnects":     discs,
			"connected":      client.IsConnected(),
			"logged_in":      client.IsLoggedIn(),
			"started":        bridgeStarted.Format(time.RFC3339),
			"policy":         p,
			"paused":         paused,
			"sent_today":     sentToday,
			"drafts_pending": draftsPending,
			"daily_cap":      envInt("WA_DAILY_CAP", 150),
		})
	})

	// POST a partial policy: {"mode":"auto","auto_until":"..."} / {"paused_until":""} / {"allow":[...]}
	http.HandleFunc("/api/policy", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			policyMu.Lock()
			p := loadPolicy()
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				policyMu.Unlock()
				http.Error(w, "bad json", http.StatusBadRequest)
				return
			}
			if p.Mode != "auto" {
				p.Mode, p.AutoUntil = "draft", ""
			}
			savePolicy(p)
			policyMu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(loadPolicy())
	})
}
