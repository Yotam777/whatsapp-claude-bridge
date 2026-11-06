# Changelog

## 1.0.1 — 2026-11-06
תשובות בכתב נטוי עם ציטוט ההודעה שלך · !!! מבטל את תקרת העלות לשעה · Claude זול יותר (Haiku לשאלות קצרות, בלי טעינת כלים מיותרים) · תקרה יומית ברירת מחדל 20$ / Italic replies quoting your message · !!! lifts the cost cap for an hour · cheaper Claude runs · 20 USD default cap

## 1.0.0 — 2026-11-06
First release of the enhanced fork.

**Bridge**
- REST API protected by a bearer token; push events `/api/events`; `/api/status`, `/api/policy`, `/api/react`, `/api/typing`, `/api/create_group`, `/api/leave_group`.
- Setup creates the private "Claude" control group automatically (no manual group creation).
- Outgoing messages stored (`send_log`); media downloaded on arrival with unique file names; fixed upstream 403 on media download (URL tokens kept).
- Polls and decrypted votes; edits and deletions; LID-addressed chats stored under the phone number; WAL databases; connection log.
- Send policy: drafts-only by default, time-limited auto mode, health guard (pause on throttling / temporary ban), human-like pacing, quoted replies.

**Agent**
- Chat with Claude from a WhatsApp control group: 👀/✅ reactions, progress note, files in and out, durable command queue, daily session rotation with hand-off, daily budget cap, light model for short questions.
- Local voice transcription (optional extra), Windows notifications on disconnect, nightly backup with retention, log rotation, plugins, Hebrew/English.

**Dashboard** — `WhatsAppDash.exe` (offline, tray icon, light/dark, Hebrew/English, self-update).

**MCP server** — `get_status`, `search_all`, `list_transcripts`, `list_polls`, `list_drafts`, `react_to_message`, quoted replies in `send_message`.

**Tooling** — `setup.cmd` wizard, `tools/selftest.py` (21 checks), Go and Python unit tests, GitHub Actions build and release.
