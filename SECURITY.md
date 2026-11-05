# Security notes

- **Everything stays on your PC.** Messages, media, transcripts and backups are stored locally (`whatsapp-bridge/store`,
  `agent/agent.db`, the backup folder). Nothing is sent anywhere except to WhatsApp and, for questions you ask, to Claude.
- **Bridge API** listens on `127.0.0.1` only and requires `Authorization: Bearer <token>` (`whatsapp-bridge/store/api_token`,
  generated on first start). Treat the `store` folder as secret: it also holds your WhatsApp session keys.
- **Dashboard** binds to `127.0.0.1` and only accepts same-origin actions. It has no password: anyone who can use your Windows
  account can open it.
- **The agent runs Claude Code headless with `--permission-mode auto`** in `agent/home`. Claude can use your PC within Claude Code's
  permission rules. Only messages *you* write in your control group are passed to it as instructions; messages from other people
  are treated as data. If you want WhatsApp instructions to count like terminal instructions, add an `autoMode.allow` rule to
  your own Claude Code settings — it is deliberately not part of this repo.
- **Sending to other people is draft-only by default.** Auto-send must be switched on explicitly and expires.
- **Unofficial client.** WhatsApp may restrict accounts that automate messaging. The bridge paces sends, never retries an uncertain
  send, and pauses itself on throttling signals — do not work around a pause.

Report security problems privately via GitHub Security Advisories on this repository.
