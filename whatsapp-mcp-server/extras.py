"""Extra read/act functions for the MCP server, backed by the enhanced bridge and the agent's database."""
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

import requests

_LOCAL = requests.Session()
_LOCAL.trust_env = False  # the bridge is on this PC: no system / antivirus proxy

from whatsapp import MESSAGES_DB_PATH, TOOL_ROOT, WHATSAPP_API_BASE_URL, _auth

ROOT = TOOL_ROOT
AGENT_DB = os.path.join(ROOT, "agent", "agent.db")


def _rows(db: str, sql: str, args=()) -> List[Dict[str, Any]]:
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def status() -> Dict[str, Any]:
    try:
        return _LOCAL.get(f"{WHATSAPP_API_BASE_URL}/status", headers=_auth(), timeout=10).json()
    except Exception as e:
        return {"error": str(e)}


def transcripts(query: Optional[str], chat_jid: Optional[str], limit: int) -> List[Dict[str, Any]]:
    sql, args = "SELECT id, chat_jid, sender, ts, text FROM transcripts WHERE 1=1", []
    if query:
        sql += " AND text LIKE ?"
        args.append(f"%{query}%")
    if chat_jid:
        sql += " AND chat_jid = ?"
        args.append(chat_jid)
    return _rows(AGENT_DB, sql + " ORDER BY ts DESC LIMIT ?", args + [limit])


def polls(chat_jid: Optional[str], limit: int) -> List[Dict[str, Any]]:
    sql, args = "SELECT poll_id, chat_jid, creator, question, options, timestamp FROM polls", []
    if chat_jid:
        sql += " WHERE chat_jid = ?"
        args.append(chat_jid)
    out = _rows(MESSAGES_DB_PATH, sql + " ORDER BY timestamp DESC LIMIT ?", args + [limit])
    for p in out:
        p["options"] = json.loads(p["options"] or "[]")
        votes = _rows(MESSAGES_DB_PATH, "SELECT voter, options, timestamp FROM poll_votes WHERE poll_id = ?", (p["poll_id"],))
        p["votes"] = [{"voter": v["voter"], "chose": json.loads(v["options"] or "[]"), "at": v["timestamp"]} for v in votes]
        p["tally"] = {o: sum(o in v["chose"] for v in p["votes"]) for o in p["options"]}
    return out


def drafts(status_: str, limit: int) -> List[Dict[str, Any]]:
    return _rows(MESSAGES_DB_PATH, "SELECT id, recipient, name, message, created, status FROM drafts WHERE status = ? "
                                   "ORDER BY id DESC LIMIT ?", (status_, limit))


def react(chat_jid: str, message_id: str, emoji: str, sender: str = "") -> Dict[str, Any]:
    try:
        return _LOCAL.post(f"{WHATSAPP_API_BASE_URL}/react", json={"chat_jid": chat_jid, "message_id": message_id,
                             "sender": sender, "emoji": emoji}, headers=_auth(), timeout=30).json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def search(query: str, limit: int) -> List[Dict[str, Any]]:
    """Text search over messages and voice transcripts together. "a|b" matches either word (Hebrew forms: "ביצה|ביצים")."""
    terms = [t.strip() for t in query.split("|") if t.strip()] or [query]
    like = " OR ".join(["m.content LIKE ?"] * len(terms))
    msgs = _rows(MESSAGES_DB_PATH, "SELECT m.id, m.chat_jid, c.name AS chat_name, m.sender, m.timestamp, m.content FROM messages m "
                                   f"LEFT JOIN chats c ON c.jid = m.chat_jid WHERE ({like}) ORDER BY m.timestamp DESC LIMIT ?",
                 [f"%{t}%" for t in terms] + [limit])
    for m in msgs:
        m["kind"] = "message"
    tr = [t for term in terms for t in transcripts(term, None, limit)]
    tr = list({t["id"]: t for t in tr}.values())
    for t in tr:
        t["kind"] = "voice transcript"
        t["timestamp"], t["content"] = t.pop("ts"), t.pop("text")
    return sorted(msgs + tr, key=lambda r: str(r.get("timestamp")), reverse=True)[:limit]


# ---------- one conversation, voice notes as text ----------

WA_DB = os.path.join(os.path.dirname(MESSAGES_DB_PATH), "whatsapp.db")


def _ts(v: Optional[str]) -> Optional[str]:
    """ISO date/time → the "YYYY-MM-DD HH:MM:SS" form the bridge's timestamps sort with."""
    if not v:
        return None
    from datetime import datetime
    return datetime.fromisoformat(v.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")


def _users_named(q: str) -> List[str]:
    """Phone / LID user parts of contacts whose saved name, first name or WhatsApp name contains q."""
    rows = _rows(WA_DB, "SELECT their_jid FROM whatsmeow_contacts WHERE full_name LIKE ? OR first_name LIKE ? OR push_name LIKE ?",
                 (f"%{q}%",) * 3)
    users = {r["their_jid"].split("@")[0] for r in rows if r.get("their_jid")}
    for u in list(users):  # the same person under their LID (newer WhatsApp chats are keyed by it)
        for r in _rows(WA_DB, "SELECT lid FROM whatsmeow_lid_map WHERE pn = ?", (u,)):
            users.add(r["lid"])
    return sorted(users)


def resolve_chat(chat: str) -> List[Dict[str, Any]]:
    """Chats matching a JID, a phone number or a (part of a) contact / group name — most recently active first."""
    chat = (chat or "").strip()
    order = " ORDER BY last_message_time DESC"
    if "@" in chat:
        return _rows(MESSAGES_DB_PATH, "SELECT jid, name, last_message_time FROM chats WHERE jid = ?", (chat,)) or [{"jid": chat, "name": chat}]
    digits = "".join(ch for ch in chat if ch.isdigit())
    if digits and len(digits) >= 7 and len(digits) >= len(chat.replace(" ", "").replace("-", "").replace("+", "")):
        if digits.startswith("0"):  # local Israeli form 05x… → 9725x…
            digits = "972" + digits[1:]
        users = [digits] + [r["lid"] for r in _rows(WA_DB, "SELECT lid FROM whatsmeow_lid_map WHERE pn = ?", (digits,))]
    else:
        users = _users_named(chat)
    found = {r["jid"]: r for r in _rows(MESSAGES_DB_PATH, "SELECT jid, name, last_message_time FROM chats WHERE name LIKE ?" + order,
                                        (f"%{chat}%",))}
    for u in users:
        for r in _rows(MESSAGES_DB_PATH, "SELECT jid, name, last_message_time FROM chats WHERE jid LIKE ?", (f"{u}@%",)):
            found.setdefault(r["jid"], r)
    return sorted(found.values(), key=lambda r: str(r.get("last_message_time") or ""), reverse=True)


def chat_history(chat: str, after: Optional[str], before: Optional[str], limit: int) -> Dict[str, Any]:
    from datetime import datetime, timedelta
    matches = resolve_chat(chat)
    if not matches:
        return {"error": f"no chat matches {chat!r} — try another spelling, a phone number, or search_contacts"}
    jid = matches[0]["jid"]
    after = _ts(after) or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    sql, args = "SELECT id, sender, timestamp, content, is_from_me, media_type FROM messages WHERE chat_jid = ? AND timestamp > ?", [jid, after]
    if before:
        sql += " AND timestamp < ?"
        args.append(_ts(before))
    rows = _rows(MESSAGES_DB_PATH, sql + " ORDER BY timestamp DESC LIMIT ?", args + [int(limit)])[::-1]
    voice = {r["id"]: r["text"] for r in _rows(AGENT_DB, "SELECT id, text FROM transcripts WHERE chat_jid = ?", (jid,))} if rows else {}
    names: Dict[str, str] = {}
    out = []
    for r in rows:
        if r["is_from_me"]:
            who = "me"
        else:
            s = r["sender"] or ""
            if s not in names:
                names[s] = _sender_name(s)
            who = names[s]
        text = r["content"] or ""
        if r["media_type"] == "audio":
            t = voice.get(r["id"])
            text = f"🎙️ (voice note) {t}" if t else "🎙️ (voice note, not transcribed yet — transcribe_voice_note can transcribe it)"
        elif r["media_type"] and not text:
            text = f"[{r['media_type']}]"
        elif r["media_type"]:
            text = f"[{r['media_type']}] {text}"
        out.append({"time": str(r["timestamp"])[:16], "from": who, "text": text, "id": r["id"]})
    name = matches[0].get("name") or ""
    if not name or name.replace("+", "").isdigit():  # the bridge stored only a number: use the saved contact name
        name = _sender_name(jid) if not jid.endswith("@g.us") else (name or jid)
    res = {"chat": name, "jid": jid, "after": after, "messages": out}
    if len(matches) > 1:
        res["other_matches"] = [{"name": m.get("name"), "jid": m["jid"]} for m in matches[1:6]]
    return res


def _sender_name(sender: str) -> str:
    """Saved contact name of a sender (also when WhatsApp gives only their LID), else the bridge's chat name."""
    from whatsapp import get_sender_name
    user = sender.split("@")[0]
    users = [user] + [r["pn"] for r in _rows(WA_DB, "SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", (user,))]
    for u in users:
        rows = _rows(WA_DB, "SELECT full_name, push_name FROM whatsmeow_contacts WHERE their_jid LIKE ?", (f"{u}@%",))
        if rows and (rows[0].get("full_name") or rows[0].get("push_name")):
            return rows[0].get("full_name") or rows[0].get("push_name")
    return get_sender_name(sender)


# ---------- transcribe one older voice note on request ----------

def transcribe_voice_note(chat: str, message_id: Optional[str], wait_seconds: int) -> Dict[str, Any]:
    """Ask the agent (which has the speech model) to transcribe voice notes, and wait for the text.
    message_id = one note; without it, the untranscribed notes of the chat from the last 7 days (up to 10)."""
    import sqlite3
    import time
    from datetime import datetime, timedelta
    matches = resolve_chat(chat) if "@" not in chat else [{"jid": chat}]
    if not matches:
        return {"error": f"no chat matches {chat!r}"}
    jid = matches[0]["jid"]
    if message_id:
        ids = [message_id]
    else:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        ids = [r["id"] for r in _rows(MESSAGES_DB_PATH, "SELECT id FROM messages WHERE chat_jid=? AND media_type='audio' AND timestamp > ? "
                                                       "ORDER BY timestamp DESC LIMIT 10", (jid, since))]
    if not ids:
        return {"chat": jid, "transcripts": [], "note": "no voice notes in this chat in the last 7 days"}
    db = sqlite3.connect(AGENT_DB, timeout=30)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS transcribe_requests (id TEXT, chat_jid TEXT, at TEXT, status TEXT DEFAULT 'pending', "
                   "PRIMARY KEY (id, chat_jid))")
        have = {r[0] for r in db.execute(f"SELECT id FROM transcripts WHERE chat_jid=? AND id IN ({','.join('?' * len(ids))})", [jid] + ids)}
        for i in ids:
            if i not in have:
                db.execute("INSERT OR REPLACE INTO transcribe_requests VALUES (?, ?, ?, 'pending')", (i, jid, datetime.now().isoformat()))
        db.commit()
        deadline = time.time() + max(5, min(int(wait_seconds), 170))
        while time.time() < deadline:
            pending = db.execute(f"SELECT COUNT(*) FROM transcribe_requests WHERE chat_jid=? AND status='pending' AND id IN ({','.join('?' * len(ids))})",
                                 [jid] + ids).fetchone()[0]
            if not pending:
                break
            time.sleep(1)
        rows = {r[0]: (r[1], r[2]) for r in db.execute(f"SELECT id, ts, text FROM transcripts WHERE chat_jid=? AND id IN ({','.join('?' * len(ids))})",
                                                       [jid] + ids)}
        failed = {r[0] for r in db.execute(f"SELECT id FROM transcribe_requests WHERE chat_jid=? AND status='failed' AND id IN ({','.join('?' * len(ids))})",
                                           [jid] + ids)}
    finally:
        db.close()
    out = []
    for i in ids:
        if i in rows:
            out.append({"id": i, "time": str(rows[i][0])[:16], "text": rows[i][1]})
        else:
            out.append({"id": i, "status": "failed (the recording could not be downloaded)" if i in failed else "still transcribing — try again shortly"})
    return {"chat": jid, "transcripts": out}
