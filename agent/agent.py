"""WhatsApp agent: the always-on companion of the whatsapp-bridge (the WhatsApp ↔ Claude tool).

Runs as one background process (started at logon by Task Scheduler):
  * the owner's messages in his control group (text, voice, images, documents) are answered by a headless Claude Code
    session: 👀 reaction on receipt, progress note for long tasks, files Claude leaves in home/outbox are sent back,
    daily session rotation with a written hand-off, daily budget cap.
  * every voice note is transcribed locally (faster-whisper, Hebrew model) into the `transcripts` table.
  * new messages arrive by push (bridge /api/events) with a database sweep by insertion order as a safety net,
    so nothing is missed and nothing is handled twice.
  * watchdog: Windows notification when WhatsApp is disconnected too long or the device is logged out.
  * nightly backup; control panel (dash) on http://127.0.0.1:8770.
  * project-specific behaviour lives in plugins (config "plugins"), not here.
"""
import datetime as dt
import importlib.util
import json
import re
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.environ.get("WA_AGENT_CONFIG") or os.path.join(HERE, "config.json"), encoding="utf-8"))
def _path(v):
    """Config paths may be relative to the tool root or start with ~."""
    v = os.path.expanduser(v)
    return v if os.path.isabs(v) else os.path.join(ROOT, v)


STORE = _path(CFG["store_dir"])
LANG = CFG.get("language", "he")
OWNER = CFG.get("owner_name") or ("הבעלים" if LANG == "he" else "the owner")

# every text the agent itself writes to WhatsApp / Windows, in Hebrew and English
T = {
    "he": {
        "budget_stop": "💸 הגעתי לתקרת העלות היומית (${spent:.2f} מתוך ${budget:.0f}). כדי להמשיך בכל זאת — התחל את ההודעה ב-! (או ב-!!! לשעה שלמה בלי הגבלה)",
        "override_hour": "🔓 בלי תקרת עלות עד {until}.",
        "budget_warn": "⚠️ נוצלו ${spent:.2f} מתוך תקרה יומית של ${budget:.0f}.",
        "working": "⏳ עובד על זה, זה לוקח קצת יותר זמן…",
        "failed": "לא הצלחתי לעבד את ההודעה, נסה שוב.",
        "voice": "(הודעה קולית, תמלול)",
        "attached": "[קובץ מצורף: {path}]", "download_failed": "ההורדה נכשלה",
        "logged_out_t": "ווצאפ נותק מהחשבון", "logged_out_b": "צריך לסרוק QR מחדש — הכלי לא מקבל הודעות",
        "down_t": "ווצאפ מנותק", "down_b": "אין חיבור כבר {mins} דקות",
        "back": "✅ החיבור לווצאפ חזר אחרי כ-{mins} דקות.",
        "update": "🆕 גרסה {latest} של הכלי זמינה (מותקנת {current}).\n{notes}\n\nלעדכון: בלוח הבקרה ← \"בדוק עדכון לכל הכלי\", או הרץ update.cmd.",
        "handoff_prompt": "השיחה הזו נסגרת ותתחיל שיחה חדשה. כתוב סיכום העברה של עד 12 שורות: משימות פתוחות, החלטות, "
                          "העדפות של {owner} ודברים שחייבים לזכור. רק הסיכום, בלי הקדמה.",
        "handoff_title": "# סיכום מהשיחה הקודמת",
        "prompt": "[הודעה מ{owner} {where}, {when}]\n{text}\n\n"
                  "ענה בשפה של {owner}, קצר ולעניין. התשובה שלך תישלח אוטומטית {dest} — אל תשלח אותה בעצמך. "
                  "אם צריך להחזיר קובץ (אקסל, תמונה, PDF) — שמור אותו בתיקייה outbox והוא יישלח אחרי התשובה.",
    },
    "en": {
        "budget_stop": "💸 Daily cost cap reached (${spent:.2f} of ${budget:.0f}). Start the message with ! to continue anyway (or !!! for a whole hour).",
        "override_hour": "🔓 No cost cap until {until}.",
        "budget_warn": "⚠️ ${spent:.2f} used of the ${budget:.0f} daily cap.",
        "working": "⏳ Working on it, this takes a bit longer…",
        "failed": "I couldn't process that message, please try again.",
        "voice": "(voice message, transcript)",
        "attached": "[attached file: {path}]", "download_failed": "download failed",
        "logged_out_t": "WhatsApp logged out", "logged_out_b": "Scan the QR code again — the tool receives no messages",
        "down_t": "WhatsApp disconnected", "down_b": "No connection for {mins} minutes",
        "back": "✅ WhatsApp connection is back after about {mins} minutes.",
        "update": "🆕 Version {latest} of the tool is available (installed {current}).\n{notes}\n\nTo update: dashboard → \"Check for a tool update\", or run update.cmd.",
        "handoff_prompt": "This session is closing and a new one starts. Write a hand-off summary of up to 12 lines: open tasks, "
                          "decisions, {owner}'s preferences and things to remember. Only the summary.",
        "handoff_title": "# Summary of the previous session",
        "prompt": "[Message from {owner} {where}, {when}]\n{text}\n\n"
                  "Answer briefly in {owner}'s language. Your answer is posted {dest} automatically — do not send it yourself. "
                  "To return a file (spreadsheet, image, PDF) save it in the outbox folder; it is sent after your answer.",
    },
}[LANG]
MSG_DB = os.path.join(STORE, "messages.db")
WA_DB = os.path.join(STORE, "whatsapp.db")
AGENT_DB = os.environ.get("WA_AGENT_DB") or os.path.join(HERE, "agent.db")
HOME = os.path.join(HERE, "home")  # working dir of the headless Claude sessions
OUTBOX = os.path.join(HOME, "outbox")
LOG = os.path.join(HERE, "agent.log")
LOG_MAX = int(CFG.get("log_max_mb", 5)) * 1024 * 1024
TOKEN_FILE = os.path.join(STORE, "api_token")


def log(*a):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:  # keep agent.log, agent.log.1, agent.log.2
            if os.path.exists(LOG + ".1"):
                os.replace(LOG + ".1", LOG + ".2")
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


_schema_lock = threading.Lock()
_schema_done = set()  # databases whose schema was created in this process (adb() is called very often, from several threads)


def adb():
    db = sqlite3.connect(AGENT_DB, timeout=30)
    with _schema_lock:
        if AGENT_DB not in _schema_done:
            _create_schema(db)
            _schema_done.add(AGENT_DB)
    global _runs_migrated
    if not _runs_migrated:
        with _schema_lock:
            if not _runs_migrated:
                _migrate(db)
    return db


def _create_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS handled (id TEXT, chat_jid TEXT, at TIMESTAMP, PRIMARY KEY (id, chat_jid));
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS transcripts (id TEXT, chat_jid TEXT, sender TEXT, ts TEXT, text TEXT, model TEXT,
            PRIMARY KEY (id, chat_jid));
        CREATE TABLE IF NOT EXISTS claude_runs (at TEXT, session TEXT, cost REAL, input_tokens INTEGER,
            output_tokens INTEGER, seconds REAL, ok INTEGER);
        CREATE TABLE IF NOT EXISTS sent (at TEXT, recipient TEXT, text TEXT, ok INTEGER, detail TEXT);
        CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT, text TEXT, at TEXT,
            status TEXT DEFAULT 'pending', answered TEXT);
        CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT DEFAULT 'time', text TEXT,
            due TEXT, chat_jid TEXT, ref_ts TEXT, status TEXT DEFAULT 'active', created TEXT, fired TEXT);
        CREATE TABLE IF NOT EXISTS local_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, at TEXT, role TEXT, text TEXT, files TEXT);
    """)


def _migrate(db):
    global _runs_migrated
    if True:  # older databases: record which model each run used (dashboard cost view)
        cols = [r[1] for r in db.execute("PRAGMA table_info(claude_runs)")]
        if "model" not in cols:
            db.execute("ALTER TABLE claude_runs ADD COLUMN model TEXT")
        if "cum" not in cols:  # Claude reports the session's cumulative cost; cost = this run only, cum = what Claude reported
            db.execute("ALTER TABLE claude_runs ADD COLUMN cum REAL")
        if "chat" not in cols:  # which conversation (group jid / 'local') a run belongs to; NULL = the primary control group
            db.execute("ALTER TABLE claude_runs ADD COLUMN chat TEXT")
        ccols = [r[1] for r in db.execute("PRAGMA table_info(commands)")]
        for col, ddl in (("chat_jid", "TEXT"), ("source", "TEXT DEFAULT 'wa'"), ("sync", "INTEGER DEFAULT 0"), ("attempts", "INTEGER DEFAULT 0")):
            if col not in ccols:  # commands now carry their conversation, where they came from (wa | pc) and whether PC answers go to WhatsApp
                db.execute(f"ALTER TABLE commands ADD COLUMN {col} {ddl}")
        prev = {}  # rows written before this fix (cum NULL): turn the cumulative figure into this run's share
        for rowid, sess, cost, cum in db.execute("SELECT rowid, session, cost, cum FROM claude_runs ORDER BY at").fetchall():
            if cum is None:
                p = prev.get(sess)
                db.execute("UPDATE claude_runs SET cost = ?, cum = ? WHERE rowid = ?", (run_cost(cost, p), cost, rowid))
                cum = cost
            prev[sess] = cum
        db.commit()
        _runs_migrated = True


_runs_migrated = False


def run_cost(cum, prev):
    """This run's cost from Claude's cumulative session figure and the previous run's figure (None = first run of the session).
    A lower figure than before means a new count started (e.g. the session was rotated), so it is taken as is."""
    return cum - prev if prev is not None and cum >= prev else cum


def kv_get(k, default=None):
    with adb() as db:
        r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def kv_set(k, v):
    with adb() as db:
        db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, str(v)))


def mdb():
    return sqlite3.connect(f"file:{MSG_DB}?mode=ro", uri=True, timeout=30)


# ---------- bridge API ----------

def token():
    try:
        return open(TOKEN_FILE).read().strip()
    except OSError:
        return ""


OWNER_TOKEN_FILE = os.path.join(STORE, "owner_token")


def owner_token():
    """The bridge's owner-only token (draft approval, policy). Claude's tools never use it; falls back to the API token
    on bridges that don't have it yet."""
    try:
        t = open(OWNER_TOKEN_FILE).read().strip()
        if t:
            return t
    except OSError:
        pass
    return token()


def write_json(path, obj):
    """Atomic JSON write (temp file + rename): a reader never sees a half-written file."""
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def bridge(path, body=None, timeout=180, tok=None):
    """Call the bridge API (token-protected). Errors that carry a JSON body (drafts, refusals) are returned as data."""
    req = urllib.request.Request(CFG["bridge_url"] + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + (tok or token())})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}"}


def send(recipient, text, media=None, quoted=None):
    """Send through the bridge (send policy + pacing apply there). Never retried: a retry could double-send."""
    body = {"recipient": recipient, "message": text}
    if media:
        body["media_path"] = media
    if quoted:
        body["quoted_id"] = quoted
    try:
        try:
            res = bridge("/send", body)
        except Exception as e:
            if not _refused(e) or not wait_bridge(180):
                raise
            res = bridge("/send", body)  # the bridge was down (e.g. starting up): the first request never reached it
        ok, detail = res.get("success", False), res.get("message", "")
    except Exception as e:
        ok, detail = False, str(e)
    with adb() as db:
        db.execute("INSERT INTO sent VALUES (?, ?, ?, ?, ?)", (dt.datetime.now().isoformat(), recipient, text[:500], ok, detail))
    log("send", recipient, ok, detail[:120])
    return ok


def _refused(e):
    t = str(e)
    return "10061" in t or "refused" in t.lower()


def wait_bridge(seconds=180):
    """True once the bridge answers /status (used at start-up and when a send found it down)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            bridge("/status", timeout=5)
            return True
        except Exception:
            time.sleep(5)
    return False


RESTART_REQ = threading.Event()
RESTART_FILE = os.path.join(HERE, "restart.request")  # Claude (or anyone) creates it: the agent restarts after the current answer


def restart_now(reason):
    """Exit cleanly; run-agent.cmd starts the agent again within ~15 s and the new process reports in the group."""
    kv_set("restart_pending", json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "reason": reason}, ensure_ascii=False))
    log("restart requested:", reason)
    os._exit(0)


def report_restart():
    """After a requested restart: confirm in the group with the settings actually loaded."""
    raw = kv_get("restart_pending")
    if not raw:
        return
    kv_set("restart_pending", "")
    try:
        info = json.loads(raw)
    except Exception:
        info = {}
    wait_bridge(300)
    forced = get_override()
    reply(f"✅ עליתי מחדש ({dt.datetime.now():%H:%M}). "
          f"סשן: {'נקי לכל הודעה' if CFG.get('fresh_sessions') else 'רציף'} · "
          f"מודל: {forced or 'אוטו'} · תקרה: ${CFG.get('daily_budget_usd')}"
          + (f"\nסיבה: {info['reason']}" if info.get("reason") else ""))


# ---------- conversations: the primary control group, extra groups (groups.json) and the dashboard's local chat ----------
GROUPS_FILE = os.path.join(HERE, "groups.json")
GROUPS_DIR = os.path.join(HOME, "groups")
INBOX = os.path.join(HOME, "inbox")
LOCAL_FILES = os.path.join(HOME, "local_files")
STOP_FILE = os.path.join(HERE, "stop.request")
PRIMARY = CFG["claude_group"]
LOCAL = "local"
_ctx = threading.local()  # the conversation the command worker is answering right now (chat, src, sync)
_groups_last = None  # last groups.json that parsed (used if the file is caught mid-write)


def load_groups():
    """jid -> {name, purpose, instructions, budget_usd, ...} for every conversation Claude answers in. Re-read on every use,
    so a group connected from the dashboard works without a restart."""
    global _groups_last
    try:
        with open(GROUPS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _groups_last = raw
    except FileNotFoundError:
        raw = {}
    except Exception as e:  # half-written / broken file: keep the last good copy instead of dropping the extra groups
        log("groups.json unreadable, using the last good copy:", e)
        raw = _groups_last or {}
    g = {PRIMARY: {"name": "4M", "purpose": "4m", **(raw.get(PRIMARY) or {})}}
    for jid, v in raw.items():
        if jid not in g and jid != LOCAL and v.get("enabled", True):
            g[jid] = dict(v)
    g[LOCAL] = {"name": "שיחה מקומית", "purpose": "local", **(raw.get(LOCAL) or {})}
    return g


def K(name, chat=None):
    """Per-conversation key in kv; the primary group keeps the legacy (unsuffixed) keys so its session survives the upgrade."""
    return name if not chat or chat == PRIMARY else f"{name}:{chat}"


def cur_chat():
    return getattr(_ctx, "chat", None) or PRIMARY


def budget_for(chat):
    return float((load_groups().get(chat or PRIMARY) or {}).get("budget_usd") or CFG.get("daily_budget_usd", 5))


def handoff_path(chat):
    if not chat or chat == PRIMARY:
        return os.path.join(HOME, "handoff.md")
    g = load_groups().get(chat) or {}
    return os.path.join(HOME, f"handoff-{g.get('slug') or re.sub(r'[^0-9a-zA-Z]+', '', chat.split('@')[0])[:12] or 'chat'}.md")


def group_prompt(chat):
    """Extra system prompt for a non-primary conversation: its instructions file (agent/home/groups/*.md) plus the hand-off file name."""
    if not chat or chat == PRIMARY:
        return None
    g = load_groups().get(chat) or {}
    parts = [f"שיחה: {g.get('name', chat)} (מטרה: {g.get('purpose', '')}). זו שיחה נפרדת — לכל שיחה סשן, הוראות ומודל משלה."]
    ins = g.get("instructions")
    if ins:
        try:
            parts.append(open(os.path.join(HOME, ins), encoding="utf-8").read().strip())
        except OSError:
            pass
    parts.append(f"בתחילת סשן חדש קרא את {handoff_path(chat)} אם קיים (סיכום מהשיחה הקודמת בשיחה הזו).")
    return "\n\n".join(parts)


def italic(text):
    """WhatsApp italics line by line (a marker can't span lines), so the tool's messages look different from the owner's.
    Lines with underscores or code are left as they are."""
    out = []
    for line in text.split("\n"):
        body = line.strip()
        if not body or "_" in body or "```" in body:
            out.append(line)
        else:
            out.append(line[:len(line) - len(line.lstrip())] + "_" + body + "_")
    return "\n".join(out)


def pc_only():
    """True while answering a message that came from the dashboard and must not be posted to WhatsApp (local chat, or sync off)."""
    return cur_chat() == LOCAL or (getattr(_ctx, "src", "wa") == "pc" and not getattr(_ctx, "sync", 0))


def local_say(chat, role, text, files=None):
    with adb() as db:
        db.execute("INSERT INTO local_messages (chat, at, role, text, files) VALUES (?, ?, ?, ?, ?)",
                   (chat, dt.datetime.now().isoformat(timespec="seconds"), role, text, json.dumps(files or [], ensure_ascii=False)))


def reply(text, quoted=None):
    chat = cur_chat()
    if chat == PRIMARY:
        kv_set("last_tool_msg", json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "text": text.strip()[:300]}, ensure_ascii=False))
    if pc_only():  # answer shown in the dashboard chat only
        local_say(chat, "tool", text.strip())
        return
    body = italic(text.strip()) if CFG.get("reply_italic", True) else text.strip()
    send(chat, CFG["reply_prefix"] + body, quoted=quoted)


def react(msg_id, emoji):
    if pc_only() or getattr(_ctx, "src", "wa") == "pc":
        return
    try:
        bridge("/react", {"chat_jid": cur_chat(), "message_id": msg_id, "sender": "", "emoji": emoji}, timeout=20)
    except Exception as e:
        log("react failed", e)


def media_path(chat_jid, msg_id):
    """Local file of a media message; asks the bridge to download it if it is not on disk yet."""
    with mdb() as db:
        r = db.execute("SELECT filename FROM messages WHERE id=? AND chat_jid=?", (msg_id, chat_jid)).fetchone()
    if r and r[0]:
        p = os.path.join(STORE, chat_jid.replace(":", "_"), r[0])
        if os.path.exists(p):
            return p
    try:
        res = bridge("/download", {"message_id": msg_id, "chat_jid": chat_jid}, timeout=300)
        return res.get("path") if res.get("success") else None
    except Exception as e:
        log("download failed", msg_id, e)
        return None


def phone_of(chat_jid):
    """Phone number behind a private chat, whether keyed by phone or by LID."""
    user = chat_jid.split("@")[0]
    if chat_jid.endswith("@lid"):
        with sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True) as w:
            r = w.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid=?", (user,)).fetchone()
        return r[0] if r else None
    return user


def notify(title, body):
    """Windows toast notification (works without WhatsApp)."""
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
          "$x=$t.GetElementsByTagName('text');"
          f"$x.Item(0).AppendChild($t.CreateTextNode('{title}'))>$null; $x.Item(1).AppendChild($t.CreateTextNode('{body}'))>$null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
          "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    except Exception as e:
        log("notify failed", e)
    log("notify:", title, body)


# ---------- transcription ----------

_whisper = None
_whisper_lock = threading.Lock()


def decode_audio(path):
    """16 kHz mono float32 samples. Done here because faster-whisper's own decoder breaks on PyAV >= 14."""
    import av
    import numpy as np
    res = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(path) as c:
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in res.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
    return (np.concatenate(chunks).astype(np.float32) / 32768.0) if chunks else np.zeros(0, np.float32)


_voice_ok = None


def voice_enabled():
    """Transcription is optional (config "transcribe", extra dependency `voice`)."""
    global _voice_ok
    if _voice_ok is None:
        try:
            import faster_whisper  # noqa: F401
            _voice_ok = bool(CFG.get("transcribe", True))
        except ImportError:
            _voice_ok = False
            log("voice transcription disabled: install with `uv sync --extra voice`")
    return _voice_ok


def transcribe(path):
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            log("loading whisper model", CFG["whisper_model"])
            _whisper = WhisperModel(CFG["whisper_model"], device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(decode_audio(path), language=CFG.get("whisper_language", "he"), vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segs).strip()


def transcript_of(chat_jid, msg_id, sender, ts):
    with adb() as db:
        r = db.execute("SELECT text FROM transcripts WHERE id=? AND chat_jid=?", (msg_id, chat_jid)).fetchone()
    if r:
        return r[0]
    p = media_path(chat_jid, msg_id)
    if not p:
        return None
    t0 = time.time()
    text = transcribe(p)
    with adb() as db:
        db.execute("INSERT OR REPLACE INTO transcripts VALUES (?, ?, ?, ?, ?, ?)", (msg_id, chat_jid, sender, ts, text, CFG["whisper_model"]))
    log(f"transcribed {msg_id} in {time.time() - t0:.0f}s: {text[:80]}")
    return text


# ---------- headless Claude ----------

def _detail_of(name, inp):
    """Short, safe detail of a tool call: a file name, a search pattern, the start of a command, a web host."""
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    if name in ("Grep", "Glob"):
        return str(inp.get("pattern", ""))[:30]
    if name in ("Bash", "PowerShell"):
        c = re.sub(r"\s+", " ", str(inp.get("description") or inp.get("command") or "")).strip()
        return c[:48] + ("…" if len(c) > 48 else "")
    if name == "WebFetch":
        return re.sub(r"^https?://([^/]+).*$", r"\1", str(inp.get("url", "")))
    if name == "WebSearch":
        return str(inp.get("query", ""))[:40]
    return ""


def _stage_of(ev):
    """Human status (Hebrew) for one stream event of Claude Code, or None."""
    if ev.get("type") != "assistant":
        return None
    for part in (ev.get("message") or {}).get("content") or []:
        if part.get("type") == "tool_use":
            name, inp = part.get("name", ""), part.get("input") or {}
            if name == "Read":
                return "📖 קורא קבצים"
            if name in ("Edit", "Write", "NotebookEdit"):
                return "✏️ עורך קוד/קבצים"
            if name in ("Grep", "Glob"):
                return "🔎 מחפש בקבצים"
            if name in ("WebSearch", "WebFetch"):
                return "🌐 מחפש ברשת"
            if name in ("Agent", "Workflow"):
                return "🧩 מפעיל סוכן עזר"
            if name in ("Bash", "PowerShell"):
                c = str(inp.get("command", ""))
                if re.search(r"(^|[\s;&|])git\s", c):
                    return "🔀 עובד עם גיט"
                if re.search(r"go build|go vet|release|pyinstaller", c):
                    return "🛠️ מקמפל/בונה"
                if "wa_send" in c:
                    return "📨 מכין טיוטה"
                return "⚙️ מריץ פקודה"
            return "⚙️ " + name
        if part.get("type") == "thinking":
            return "🧠 חושב"
    return None


class Stopped(Exception):
    pass


def _kill_tree(proc):
    """Kill Claude and everything it started (MCP servers, shells): a bare kill leaves children holding the pipe open."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        except Exception as e:
            log("taskkill failed", e)
    try:
        proc.kill()
    except Exception:
        pass


_active_runs = {}  # chat -> start time of the interactive Claude run answering it now (stop button / restart coordination)
_active_lock = threading.Lock()


def stop_target():
    """Which conversation the dashboard's stop.request is for: (exists, chat or '' for a legacy/empty request)."""
    try:
        with open(STOP_FILE, encoding="utf-8") as f:
            return True, f.read().strip()
    except OSError:
        return False, ""


def stop_applies(target, chat, active):
    """A stop request names its conversation; an empty (legacy) request stops the primary group's run, or the only run."""
    if target:
        return target == chat
    return chat == PRIMARY or len(active) <= 1


def _run_claude(prompt, model, resume, timeout, on_stage=None, append=None, chat=None, tools=None, max_budget=None, stoppable=None):
    """One headless Claude Code run; the prompt goes in on stdin (no command-line length limit, no cmd.exe quoting).
    tools="" = a restricted run: no tools and no MCP servers at all (for classifying other people's text)."""
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "auto"]
    if model:
        cmd += ["--model", model]
    if append:
        cmd += ["--append-system-prompt", append]
    if tools is not None:
        cmd += ["--tools", tools, "--strict-mcp-config"]  # --tools alone still loads MCP connector tools (e.g. Gmail send)
    elif CFG.get("strict_mcp"):
        cmd += ["--strict-mcp-config"]  # skip the heavy connector tool lists (Gmail, Shopify...) to cut tokens
    if max_budget:
        cmd += ["--max-budget-usd", f"{float(max_budget):.2f}"]
    if resume:
        cmd += ["--resume", resume]
    chat = chat or PRIMARY
    if stoppable is None:
        stoppable = on_stage is not None  # only the owner's interactive runs; plugin / hand-off runs never eat a stop request
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=HOME, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError as e:
        log("claude stdin failed", e)
    killer = threading.Timer(timeout, _kill_tree, args=(proc,))
    killer.start()
    stopped = threading.Event()
    if stoppable:
        with _active_lock:
            _active_runs[chat] = t0
        exists, target = stop_target()
        if exists and stop_applies(target, chat, _active_runs):
            try:
                os.remove(STOP_FILE)  # a stop request for this conversation left over from before this run must not kill it
            except OSError:
                pass

        def watch_stop():  # the dashboard's "stop" button drops a stop.request file naming the conversation
            while proc.poll() is None:
                exists, target = stop_target()
                if exists:
                    with _active_lock:
                        active = dict(_active_runs)
                    if stop_applies(target, chat, active):
                        try:
                            os.remove(STOP_FILE)
                        except OSError:
                            pass
                        stopped.set()
                        _kill_tree(proc)
                        return
                time.sleep(1)
        threading.Thread(target=watch_stop, daemon=True).start()
    res = {}
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "result":
                res = ev
            elif on_stage:
                st = _stage_of(ev)
                if st:
                    det = ""
                    for part in (ev.get("message") or {}).get("content") or []:
                        if part.get("type") == "tool_use":
                            det = _detail_of(part.get("name", ""), part.get("input") or {})
                    on_stage(st, det)
        proc.wait()
    finally:
        killer.cancel()
        if stoppable:
            with _active_lock:
                if _active_runs.get(chat) == t0:
                    _active_runs.pop(chat, None)
    if stopped.is_set():
        raise Stopped()
    if not res:
        raise RuntimeError("claude returned no result")
    usage = res.get("usage", {})
    with adb() as db:
        # total_cost_usd is cumulative for a resumed session: store only this run's share (budget + dashboard sum `cost`)
        cum = float(res.get("total_cost_usd") or 0)
        prev = db.execute("SELECT cum FROM claude_runs WHERE session = ? AND cum IS NOT NULL ORDER BY at DESC LIMIT 1",
                          (res.get("session_id"),)).fetchone() if resume else None
        cost = run_cost(cum, prev[0] if prev else None)
        db.execute("INSERT INTO claude_runs (at, session, cost, input_tokens, output_tokens, seconds, ok, model, cum, chat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (dt.datetime.now().isoformat(), res.get("session_id"), cost, usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0), time.time() - t0, 0 if res.get("is_error") else 1, model or CFG.get("claude_model") or "", cum,
                    chat))
    return res


def write_handoff(old_sid, chat=None):
    """Before a session is rotated, ask it for a short hand-off so the next session keeps the thread."""
    try:
        # the cheap model: this run re-reads the whole day's context (the CLI default may be the most expensive model)
        hmodel = CFG.get("light_model") or CFG.get("claude_model") or "sonnet"
        res = _run_claude(T["handoff_prompt"].format(owner=OWNER), hmodel, old_sid, 300, append=group_prompt(chat), chat=chat)
        if res.get("result"):
            with open(handoff_path(chat), "w", encoding="utf-8") as f:
                f.write(f"{T['handoff_title']} ({dt.datetime.now():%d.%m.%Y %H:%M})\n\n{res['result'].strip()}\n")
            log("handoff written")
    except Exception as e:
        log("handoff failed", e)


DEV_WORDS = ("גשר", "סוכן", "agent.py", "bridge", "whatsapp-mcp", "דשבורד", "לוח הבקרה", "קוד", "סקריפט", "go build", "release", "models.json", "wa_send", "מודל", "אוטומציה")


def is_dev_work(text):
    """True when the message is about the WhatsApp bridge / agent code itself (not the owner's business work) - the 60-message session cap doesn't apply."""
    t = (text or "").lower()
    return any(w.lower() in t for w in DEV_WORDS)


def raw_text(text):
    """The owner's own words: without the attachment line(s) the agent adds and the voice-note marker."""
    lines = [l for l in (text or "").splitlines() if not l.strip().startswith(T["attached"].split("{")[0].strip())]
    return "\n".join(lines).replace(T["voice"], "").strip()


def session_rotates(chat, raw=""):
    """True when the conversation's current session ends before the next run (midnight, or the message cap for business work)."""
    g = load_groups().get(chat or PRIMARY) or {}
    sid, started = kv_get(K("session_id", chat)), kv_get(K("session_started", chat))
    if not sid:
        return False
    if started and dt.datetime.fromisoformat(started).date() != dt.date.today():
        return True
    uncapped = g.get("purpose") == "dev" or is_dev_work(raw)  # no message cap while working on the bridge/agent code
    return not uncapped and int(kv_get(K("session_runs", chat)) or 0) >= int(CFG.get("session_max_runs", 60))


def session_active(chat, raw=""):
    """True when the next run resumes an existing session (so switching models would throw away its prompt cache)."""
    return bool(kv_get(K("session_id", chat or PRIMARY))) and not session_rotates(chat or PRIMARY, raw) and not CFG.get("fresh_sessions")


def claude(prompt, model=None, session=True, timeout=900, on_stage=None, chat=None, raw=None, tools=None, max_budget=None,
           budget_check=False):
    """Run Claude Code headless in HOME. Each conversation (group / local chat) has its own session, resumed until it is
    rotated (daily, with hand-off; and after session_max_runs for business conversations).
    raw = the owner's own text (decides "dev work" — not the wrapped prompt). tools="" = restricted run (no tools, no MCP).
    budget_check = refuse when this conversation's cap or the total daily cap is used up (plugin / background runs)."""
    chat = chat or PRIMARY
    if budget_check:
        why = over_budget(chat)
        if why:
            log(f"claude skipped ({why}) for", chat)
            return None
    sid = kv_get(K("session_id", chat)) if session else None
    if sid and session_rotates(chat, prompt if raw is None else raw):
        write_handoff(sid, chat)
        sid = None
    try:
        res = _run_claude(prompt, model, sid, timeout, on_stage, append=group_prompt(chat), chat=chat, tools=tools, max_budget=max_budget)
    except Stopped:
        return "⛔ עצרתי את העבודה לבקשתך."
    except Exception as e:
        log("claude failed", e)
        return None
    if session and res.get("session_id"):
        if res["session_id"] != kv_get(K("session_id", chat)):
            kv_set(K("session_started", chat), dt.datetime.now().isoformat())
            kv_set(K("session_runs", chat), "0")
            kv_set(K("session_model", chat), "")  # a new session: no model yet
        kv_set(K("session_id", chat), res["session_id"])
        kv_set(K("session_runs", chat), str(int(kv_get(K("session_runs", chat)) or 0) + 1))
        if model and getattr(_ctx, "auto_pick", False):
            kv_set(K("session_model", chat), model)  # automatic routing stays on this model for the rest of the session
    return res.get("result")


def spent_today(chat=None):
    """Spend today: for one conversation (chat) or, with no argument, in total."""
    q, args = "SELECT coalesce(sum(cost),0) FROM claude_runs WHERE substr(at,1,10) = ?", [dt.date.today().isoformat()]
    if chat:
        q += " AND (chat = ? OR (chat IS NULL AND ? = ?))"
        args += [chat, chat, PRIMARY]
    with adb() as db:
        return db.execute(q, args).fetchone()[0]


def total_budget():
    """Daily cap over all conversations together (each one also has its own cap)."""
    v = CFG.get("total_daily_budget_usd")
    return float(v) if v else 2 * float(CFG.get("daily_budget_usd", 5))


def cap_lifted(chat, text=""):
    """`!` on this message, or `!!!` earlier in this conversation (an hour without a cap)."""
    return (text or "").lstrip().startswith("!") or (kv_get(K("override_until", chat)) or "") > dt.datetime.now().isoformat(timespec="seconds")


def over_budget(chat, text=""):
    """None when a run may go ahead; otherwise which cap stops it: ('chat'|'total', spent, cap)."""
    if cap_lifted(chat, text):
        return None
    spent, cap = spent_today(chat), budget_for(chat)
    if spent >= cap:
        return ("chat", spent, cap)
    tot, tcap = spent_today(), total_budget()
    if tot >= tcap:
        return ("total", tot, tcap)
    return None


# The owner's questions are stored before they are answered and closed only after the reply went out,
# so a restart in the middle picks them up again instead of losing them.
_command_signal = threading.Event()


def enqueue_command(msg_id, text, when, chat=None):
    chat = chat or PRIMARY
    with adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES (?, ?, ?, ?, 'wa', 0)", (msg_id, text, when, chat))
    _ctx.chat, _ctx.src, _ctx.sync = chat, "wa", 0
    try:
        react(msg_id, "👀")  # immediate "got it"
    finally:
        _ctx.chat = None
    _command_signal.set()


def send_outbox(since):
    """Files Claude saved in home/outbox during the answer are sent to the owner (WhatsApp, or shown in the dashboard chat), then archived."""
    if not os.path.isdir(OUTBOX):
        return
    done = os.path.join(OUTBOX, "sent")
    os.makedirs(done, exist_ok=True)
    for f in sorted(os.listdir(OUTBOX)):
        p = os.path.join(OUTBOX, f)
        if os.path.isfile(p) and os.path.getmtime(p) >= since - 5:
            stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{f}"
            if pc_only():
                os.makedirs(LOCAL_FILES, exist_ok=True)
                shutil.copy(p, os.path.join(LOCAL_FILES, stamp))
                local_say(cur_chat(), "tool", "📎 " + f, [stamp])
            else:
                send(cur_chat(), CFG["reply_prefix"] + "📎 " + f, media=p)
            shutil.move(p, os.path.join(done, stamp))


MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_override.json")
MODELS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json"), encoding="utf-8"))


def _override_state():
    try:
        return json.load(open(MODEL_FILE, encoding="utf-8"))
    except Exception:
        return {}


def get_override(chat=None):
    """Model forced by the owner for this conversation (WhatsApp /מודל or the dashboard), or None for automatic routing.
    Every conversation has its own choice; the primary group keeps the legacy top-level fields."""
    o = _override_state()
    node = o if not chat or chat == PRIMARY else (o.get("groups") or {}).get(chat) or {}
    if node.get("model") in (None, "", "auto"):
        return None
    if node.get("until") and node["until"] < dt.datetime.now().isoformat(timespec="seconds"):
        return None
    return node["model"]


def set_override(model, hours=None, chat=None):
    until = (dt.datetime.now() + dt.timedelta(hours=hours)).isoformat(timespec="seconds") if hours else ""
    o = _override_state()
    if not chat or chat == PRIMARY:
        o["model"], o["until"] = model, until
    else:
        o.setdefault("groups", {})[chat] = {"model": model, "until": until}
    write_json(MODEL_FILE, o)


def models_guide(chat=None):
    cur = get_override(chat) or "auto"
    lines = [f"*המודל כרגע: {MODELS[cur]['he']}*", ""]
    for k, m in MODELS.items():
        lines.append(f"{'👉 ' if k == cur else ''}*{m['he']}* — {m['cost']}\n{m['tip']}\nפקודה: {m['cmd']}")
    lines.append("\nאפשר להוסיף \"שעה\" כדי שהבחירה תחזור לאוטו אחרי שעה: /מודל opus שעה. מצב הסשן: /סשן, סשן חדש: /סשן חדש.")
    return "\n".join(lines)


def recommend(text, used, failed):
    """Advice only (never switches by itself): which model fits this kind of request. Returns (model, reason) or None."""
    t = text.lstrip("!").strip()
    hard = any(w in t for w in ("ארכיטקטור", "אפיון", "תכנן", "באג", "לא עובד", "נכשל", "שוב לא", "refactor"))
    code = any(w in t for w in ("קוד", "agent.py", "הגשר", "סקריפט", "קמפל", "גיט", "release"))
    if failed >= 2 or hard:
        want, why = "opus", "משימה מורכבת או כמה כישלונות ברצף"
    elif code:
        want, why = "sonnet", "עבודה על קוד"
    elif len(t) < 60 and used in ("opus", "fable"):
        want, why = "haiku", "שאלה קלה, אין צורך במודל יקר"
    else:
        return None
    if want == used or (want == "sonnet" and used in ("opus", "fable")) or (want == "opus" and used == "fable"):
        return None
    return want, why


DEEP_WORDS = ("נתח לעומק", "ניתוח מעמיק", "תכנן", "תכנון", "ארכיטקטורה", "החלטה", "תחליט", "השווה בין", "בדוק לעומק")


def pick_model_info(text, chat=None):
    """(model, how): how = 'forced' (/מודל or dashboard), 'bang' (!!), 'auto', or 'sticky_deep' (deep words in a running
    session: the session keeps its model so its prompt cache stays valid — the answer suggests !! instead).
    Automatic routing in a running session never switches models mid-session except one step up (light → strong)."""
    forced = get_override(chat)
    if forced:
        return forced, "forced"
    s = text.lstrip()
    if s.startswith("!!") and not s.startswith("!!!"):  # "!!" = this one message on the strongest model (owner request 07.11)
        return CFG.get("deep_model") or "opus", "bang"
    t = text.lstrip("!").strip()
    heavy_words = ("תמלל", "סכם", "סיכום", "נתח", "ניתוח", "דוח", "דו\"ח", "אקסל", "קובץ", "תקן", "קוד", "סקריפט", "הגדרות",
                   "שלח ל", "טיוטה", "מייל", "הצעת", "השווה", "תכנן", "למה", "בדוק לעומק", "ערוך", "תוסיף", "תשנה", "תעדכן את הקוד")
    heavy = len(t) > 140 or T["attached"].split("{")[0] in text or any(w in t for w in heavy_words)
    light, strong = CFG.get("light_model") or "haiku", CFG.get("claude_model") or "sonnet"
    base = strong if heavy else light
    deep = CFG.get("deep_model") if CFG.get("deep_model") and any(w in t for w in DEEP_WORDS) else None
    sm = kv_get(K("session_model", chat)) if session_active(chat, raw_text(text)) else None
    if not sm:  # a new session may start on any model
        return (deep or base), "auto"
    if deep and sm != deep:
        return (strong if sm == light and heavy else sm), "sticky_deep"
    if sm == light and base == strong:
        return strong, "auto"  # one step up is allowed (then sticky)
    return sm, "auto"


def pick_model(text, chat=None):
    """Cost routing: light model for short lookups/updates, the normal model for anything heavier (config: light_model /
    claude_model), deep_model for planning words at the start of a session; see pick_model_info."""
    return pick_model_info(text, chat)[0]


class StatusMsg:
    """One WhatsApp message that is edited while Claude works (stage, detail, elapsed time, model). At the end it is left
    in its final state (not deleted). The steps also go to kv 'live_steps' for the dashboard."""
    def __init__(self, model, chat=None, wa=True):
        self.model, self.stage, self.mid, self.t0, self.done = model, "🧠 מתחיל", None, time.time(), threading.Event()
        self.chat, self.wa = chat or PRIMARY, wa
        self.steps = []
        self._save(False)
        self.timer = threading.Timer(8, self._start)
        if wa:  # PC-only conversations show progress in the dashboard only
            self.timer.start()

    def _save(self, done):
        try:
            kv_set(K("live_steps", self.chat), json.dumps({"model": self.model, "started": dt.datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
                                             "done": done, "secs": int(time.time() - self.t0), "steps": self.steps[-8:]}, ensure_ascii=False))
        except Exception as e:
            log("live_steps save failed", e)

    def set_stage(self, st, detail=""):
        text = f"{st} · {detail}" if detail else st
        self.stage = text
        if not self.steps or self.steps[-1]["text"] != text:
            self.steps.append({"at": dt.datetime.now().isoformat(timespec="seconds"), "text": text})
            self._save(False)

    def _elapsed(self):
        e = int(time.time() - self.t0)
        return f"{e // 60}:{e % 60:02d}"

    def _text(self):
        return CFG["reply_prefix"] + italic(f"⏳ {self.stage} · {self._elapsed()} · {self.model}")

    def _final(self):
        n = len(self.steps)
        return CFG["reply_prefix"] + italic(f"✅ סיימתי · {self._elapsed()} · {self.model}" + (f" · {n} שלבים" if n else ""))

    def _start(self):
        if self.done.is_set():
            return
        text = self._text()
        send(self.chat, text)
        for _ in range(8):
            try:
                with mdb() as db:
                    r = db.execute("SELECT id FROM messages WHERE chat_jid = ? AND is_from_me = 1 AND content = ? ORDER BY timestamp DESC LIMIT 1",
                                   (self.chat, text)).fetchone()
                if r:
                    self.mid = r[0]
                    break
            except Exception:
                pass
            time.sleep(1)
        last = text
        while self.mid and not self.done.wait(10):
            new = self._text()
            if new != last:
                try:
                    bridge("/edit_message", {"jid": self.chat, "id": self.mid, "text": new}, timeout=20)
                    last = new
                except Exception as e:
                    log("status edit failed", e)
        if self.done.is_set() and self.mid:  # leave the final state (owner request 07.11): no delete
            try:
                bridge("/edit_message", {"jid": self.chat, "id": self.mid, "text": self._final()}, timeout=20)
            except Exception as e:
                log("status final edit failed", e)

    def finish(self):
        self._save(True)
        self.done.set()
        self.timer.cancel()


def owner_command(text, chat=None):
    """Slash commands answered locally (no Claude run, no cost), for this conversation. Returns reply text or None."""
    if re.match(r"^/(הפעל מחדש|אתחל|restart)\s*$", text.strip()):
        RESTART_REQ.set()
        return "🔄 מפעיל את הסוכן מחדש. אכתוב כאן כשאעלה (בערך 20 שניות)."
    m = re.match(r"^/(מודל|model|מודלים|סשן|session)\s*(.*)$", text.strip(), re.S)
    if not m:
        return None
    cmd, arg = m.group(1), m.group(2).strip().lower()
    if cmd in ("סשן", "session"):
        sid = kv_get(K("session_id", chat))
        if arg in ("חדש", "new", "reset"):
            if sid:
                write_handoff(sid, chat)
            kv_set(K("session_id", chat), "")
            kv_set(K("session_runs", chat), "0")
            return f"🔄 הסשן נסגר (נכתב {os.path.basename(handoff_path(chat))}). ההודעה הבאה פותחת שיחה חדשה עם הסיכום."
        fresh = CFG.get("fresh_sessions")
        return (f"סשן: {'נקי לכל הודעה (fresh_sessions)' if fresh else 'רציף'} · הודעות בסשן: {kv_get(K('session_runs', chat)) or 0}"
                f" מתוך {CFG.get('session_max_runs', 60)} (אז מתחלף עם סיכום) · התחיל: {(kv_get(K('session_started', chat)) or '-')[:16]}")
    if not arg:
        return models_guide(chat)
    parts = arg.replace("שעה", "hour").split()
    name = {"אוטו": "auto"}.get(parts[0], parts[0])
    if name not in MODELS:
        return "לא מכיר את המודל הזה. אפשרויות: " + ", ".join(MODELS) + "\n\n" + models_guide(chat)
    hours = 1 if "hour" in parts[1:] else None
    set_override(name, hours, chat)
    return f"✅ המודל הוחלף ל-*{MODELS[name]['he']}*{' לשעה' if hours else ''} ({MODELS[name]['cost']}). חזרה לאוטו: /מודל אוטו"


T_BACK_HE = ("[המשך מהנייד: ההודעות האחרונות בשיחה הזו נכתבו בלוח הבקרה במחשב ולא הופיעו בקבוצה בווצאפ. "
             "המשך מאותה נקודה כאילו זו אותה שיחה; אם התשובה נשענת על מה שנעשה במחשב, פתח בשורה קצרה שמסכמת אותו כדי שהקבוצה תבין.]\n")
T_BACK_EN = ("[Continued from the phone: the latest turns of this conversation were typed in the PC dashboard and never appeared in the "
             "WhatsApp group. Continue from the same point; if the answer relies on that PC work, open with one short line recapping it.]\n")


def came_back_from_pc(chat, cid):
    """True when the previous command in this conversation was a PC-only one (dashboard, WhatsApp sync off): the phone
    message continues that same session, and the WhatsApp group hasn't seen those turns."""
    with adb() as db:
        prev = db.execute("SELECT source, sync FROM commands WHERE chat_jid = ? AND id < ? AND status = 'done' ORDER BY id DESC LIMIT 1",
                          (chat, cid)).fetchone()
    return bool(prev) and prev[0] == "pc" and not prev[1]


INTERRUPTED_HE = "⚠️ הפקודה נקטעה בהפעלה מחדש — שלח שוב אם צריך"
INTERRUPTED_EN = "⚠️ This command was cut off by a restart — send it again if needed"


def mark_interrupted():
    """At start-up (before the dispatcher starts): commands that were running when the agent stopped are NOT run again
    (that could double drafts, edits and cost). Returns them; notify_interrupted tells the owner."""
    with adb() as db:
        rows = db.execute("SELECT id, msg_id, chat_jid, source, sync FROM commands WHERE status = 'running'").fetchall()
        db.execute("UPDATE commands SET status = 'interrupted', answered = ? WHERE status = 'running'", (dt.datetime.now().isoformat(),))
    return rows


def notify_interrupted(rows):
    """Tell the owner, in each conversation, that a command was cut off (waits for the bridge; runs in its own thread)."""
    if rows and not pc_rows_only(rows):
        wait_bridge(300)
    for cid, mid, chat, src, sync in rows:
        _ctx.chat, _ctx.src, _ctx.sync = chat or PRIMARY, src or "wa", sync or 0
        try:
            reply(INTERRUPTED_HE if LANG == "he" else INTERRUPTED_EN, quoted=mid if (src or "wa") == "wa" else None)
        except Exception as e:
            log("interrupted notice failed", cid, e)
        finally:
            _ctx.chat = None


def pc_rows_only(rows):
    return all((src or "wa") == "pc" and not sync or (chat == LOCAL) for _, _, chat, src, sync in rows)


def claim_next(chat):
    """The next pending command of one conversation, atomically marked running (attempts + 1), or None."""
    with adb() as db:
        row = db.execute("SELECT id, msg_id, text, at, chat_jid, source, sync FROM commands WHERE status = 'pending' "
                         "AND coalesce(chat_jid, ?) = ? ORDER BY id LIMIT 1", (PRIMARY, chat)).fetchone()
        if not row:
            return None
        n = db.execute("UPDATE commands SET status = 'running', attempts = coalesce(attempts, 0) + 1 WHERE id = ? AND status = 'pending'",
                       (row[0],)).rowcount
    return row if n == 1 else claim_next(chat)


def run_command(row):
    """Answer one owner command (in the calling thread; _ctx = its conversation)."""
    cid, mid, text, when, chat, src, sync = row
    chat, src, sync = chat or PRIMARY, src or "wa", sync or 0
    _ctx.chat, _ctx.src, _ctx.sync, _ctx.auto_pick = chat, src, sync, False
    g = load_groups().get(chat) or {}
    try:
        local = owner_command(text, chat)
        if local is not None:
            reply(local, quoted=mid if src == "wa" else None)
            react(mid, "✅")
            return
        if text.lstrip().startswith("!!!"):  # lift the cap for an hour
            until = dt.datetime.now() + dt.timedelta(hours=1)
            kv_set(K("override_until", chat), until.isoformat(timespec="seconds"))
            reply(T["override_hour"].format(until=until.strftime("%H:%M")))
        stop = over_budget(chat, text)
        if stop:
            kind, spent, cap = stop
            msg = T["budget_stop"].format(spent=spent, budget=cap)
            if kind == "total":
                msg = ("(תקרה כוללת לכל השיחות) " if LANG == "he" else "(total cap, all conversations) ") + msg
            reply(msg)
            return
        budget, spent = budget_for(chat), spent_today(chat)
        if spent >= budget * 0.8 and kv_get(K("budget_warned", chat)) != dt.date.today().isoformat():
            kv_set(K("budget_warned", chat), dt.date.today().isoformat())
            reply(T["budget_warn"].format(spent=spent, budget=budget))
        gname = g.get("name") or ""
        if chat == PRIMARY and src == "wa":
            where, dest = ("בקבוצת הבקרה בווצאפ", "לקבוצה") if LANG == "he" else ("in the WhatsApp control group", "to the group")
        elif pc_only():
            where, dest = (f'מהמחשב (לוח הבקרה, "{gname}")', "לחלון השיחה בלוח הבקרה") if LANG == "he" else (f'from the PC (dashboard, "{gname}")', "to the dashboard chat")
        elif src == "pc":
            where, dest = (f'מהמחשב (לוח הבקרה) לקבוצה "{gname}"', "גם לקבוצה בווצאפ") if LANG == "he" else (f'from the PC (dashboard) for group "{gname}"', "to the WhatsApp group too")
        else:
            where, dest = (f'בקבוצה "{gname}" בווצאפ', "לקבוצה") if LANG == "he" else (f'in the WhatsApp group "{gname}"', "to the group")
        prompt = T["prompt"].format(owner=OWNER, when=when, text=text.lstrip("!").strip(), where=where, dest=dest)
        if src == "wa" and came_back_from_pc(chat, cid):
            prompt = (T_BACK_HE if LANG == "he" else T_BACK_EN) + prompt
        model, how = pick_model_info(text, chat)
        _ctx.auto_pick = how == "auto"
        started = time.time()
        status = StatusMsg(model, chat, wa=not pc_only())
        try:
            answer = claude(prompt, model=model, session=not CFG.get("fresh_sessions"), on_stage=status.set_stage, chat=chat,
                            raw=raw_text(text.lstrip("!")))
        finally:
            status.finish()
        stopped = (answer or "").startswith("⛔ עצרתי")
        streak = 0 if answer else int(kv_get(K("fail_streak", chat)) or 0) + 1
        kv_set(K("fail_streak", chat), str(streak))
        kv_set(K("last_model", chat), model)
        rec = None if stopped else recommend(text, model, streak)
        kv_set(K("last_recommend", chat), json.dumps({"model": rec[0], "why": rec[1], "at": dt.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) if rec else "")
        out = answer or T["failed"]
        if rec:
            out += f"\n\n💡 כדאי לעבור ל-{MODELS[rec[0]]['he']} ({rec[1]}). פקודה: {MODELS[rec[0]]['cmd']}"
        elif how == "sticky_deep" and not stopped:
            out += ("\n\n💡 לניתוח לעומק: התחל את ההודעה ב-!! (המודל החזק להודעה אחת; הסשן נשאר על המודל הנוכחי כדי לא לשלם שוב על כל ההקשר)."
                    if LANG == "he" else "\n\n💡 For deep analysis start the message with !! (strongest model for one message).")
        reply(out, quoted=mid if (CFG.get("quote_replies", True) and src == "wa") else None)
        send_outbox(started)
        react(mid, "✅")
    except Exception:
        log("command error", traceback.format_exc())
    finally:
        _ctx.chat, _ctx.auto_pick = None, False
        with adb() as db:
            db.execute("UPDATE commands SET status = 'done', answered = ? WHERE id = ?", (dt.datetime.now().isoformat(), cid))


_workers = set()  # conversations that have a worker thread right now
_workers_lock = threading.Lock()


def restart_wanted():
    """A restart was asked for (/הפעל מחדש or restart.request): returns the reason, or None."""
    if RESTART_REQ.is_set():
        return "/הפעל מחדש"
    if os.path.exists(RESTART_FILE):
        try:
            reason = open(RESTART_FILE, encoding="utf-8").read().strip()[:200]
        except OSError:
            reason = ""
        return reason or "בקשת Claude"
    return None


def conversation_worker(chat):
    """Answers one conversation's commands in order; other conversations run in parallel in their own workers."""
    try:
        while not restart_wanted():
            row = claim_next(chat)
            if not row:
                break
            run_command(row)
    except Exception:
        log("conversation worker error", chat, traceback.format_exc())
    finally:
        with _workers_lock:
            _workers.discard(chat)
        _command_signal.set()


def command_worker():
    """Dispatcher: one worker thread per conversation with pending commands (serial within a conversation, parallel
    across conversations). A requested restart waits until every running answer has been sent."""
    while True:
        try:
            reason = restart_wanted()
            with _workers_lock:
                busy = bool(_workers)
            if reason:
                if not busy:
                    if os.path.exists(RESTART_FILE):
                        try:
                            os.remove(RESTART_FILE)
                        except OSError:
                            pass
                    restart_now(reason)
            else:
                with adb() as db:
                    chats = [r[0] for r in db.execute("SELECT DISTINCT coalesce(chat_jid, ?) FROM commands WHERE status = 'pending'", (PRIMARY,))]
                for chat in chats:
                    with _workers_lock:
                        if chat in _workers:
                            continue
                        _workers.add(chat)
                    threading.Thread(target=conversation_worker, args=(chat,), daemon=True, name=f"conv:{chat}").start()
        except Exception:
            log("dispatcher error", traceback.format_exc())
        _command_signal.wait(3)  # short: the dashboard (a separate process) queues PC messages straight into the table
        _command_signal.clear()


# ---------- backup ----------

def backup():
    root = _path(CFG["backup_dir"])
    day = os.path.join(root, "db", dt.date.today().isoformat())
    os.makedirs(day, exist_ok=True)
    for name in ("messages.db", "whatsapp.db"):
        src = sqlite3.connect(f"file:{os.path.join(STORE, name)}?mode=ro", uri=True)
        dst = sqlite3.connect(os.path.join(day, name))
        src.backup(dst)
        dst.close(); src.close()
    shutil.copy(AGENT_DB, os.path.join(day, "agent.db"))
    # fetch any media still only on the server (last 30 days), then mirror the media folders
    cutoff = (dt.datetime.now() - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    with mdb() as db:
        todo = db.execute("SELECT id, chat_jid FROM messages WHERE media_type IN ('image','video','audio','document') AND timestamp > ?", (cutoff,)).fetchall()
    fetched = sum(1 for mid, cj in todo if media_path(cj, mid))
    copied = 0
    mroot = os.path.join(root, "media")
    for d in os.listdir(STORE):
        sd = os.path.join(STORE, d)
        if os.path.isdir(sd):
            for f in os.listdir(sd):
                dst = os.path.join(mroot, d, f)
                if not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(os.path.join(sd, f), dst)
                    copied += 1
    for old in sorted(os.listdir(os.path.join(root, "db")))[:-CFG["backup_keep_days"]]:
        shutil.rmtree(os.path.join(root, "db", old), ignore_errors=True)
    keep = time.time() - CFG.get("backup_media_days", 90) * 86400  # mirrored media older than this is dropped from the backup
    for d, _, files in os.walk(mroot):
        for f in files:
            if os.path.getmtime(os.path.join(d, f)) < keep:
                os.remove(os.path.join(d, f))
    kv_set("backup_done", dt.datetime.now().isoformat())
    log(f"backup done: dbs -> {day}, media checked {fetched}/{len(todo)}, new files copied {copied}")


# ---------- updates ----------

def update_check():
    """Daily: tell the owner once about each new release (they decide when to update)."""
    out = subprocess.run([shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe"), "run", "-q", "--no-project", "python", "-X", "utf8",
                          os.path.join(ROOT, "tools", "update.py"), "--check"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        res = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return
    if "error" in res:
        return
    write_json(os.path.join(HERE, "update_check.json"), res)
    if res.get("available") and kv_get("update_notified") != res["latest"]:
        kv_set("update_notified", res["latest"])
        reply(T["update"].format(latest=res["latest"], current=res["current"], notes=(res.get("notes") or "")[:800]))


def group_check():
    """Daily: ask WhatsApp for each connected group's current name, compare with the bridge's stored name,
    let the bridge heal the stored name, and tell the owner only if something was wrong or changed."""
    for jid, g in load_groups().items():
        if jid == LOCAL:
            continue
        try:
            res = bridge("/group_info", {"jid": jid}, timeout=60)
        except Exception as e:
            log("group_check: bridge unreachable", e)
            return
        if not res.get("success"):
            log("group_check failed:", jid, res)
            if jid == PRIMARY:
                reply(f"בדיקה יומית של שם הקבוצה נכשלה: {res.get('error') or res.get('message') or 'שגיאה לא ידועה'}")
            continue
        name, stored = res.get("name", ""), res.get("stored_name", "")
        log(f"group_check: {jid} live={name!r} stored={stored!r} healed={res.get('healed')}")
        if res.get("healed") and jid == PRIMARY:
            reply(f"שם הקבוצה השתנה ל-\"{name}\". עדכנתי את הגשר (היה רשום \"{stored}\"). המזהה לא השתנה, והחיבור תקין.")


# ---------- plugins ----------
# A plugin is a Python file with setup(agent). It may register:
#   agent.on_message(fn(row))                    called for every new message (row as in sweep())
#   agent.every(minutes, key, fn)                periodic job
#   agent.daily("HH:MM", key, fn)                once a day at/after that time
#   agent.background(fn)                         long-running thread
#   agent.manual(name, fn)                       `agent.py <name>` on the command line

_hooks, _periodic, _daily, _threads, _manual = [], [], [], [], {"backup": backup, "update_check": update_check, "group_check": group_check}
on_message = _hooks.append
background = _threads.append
every = lambda minutes, key, fn: _periodic.append((minutes, key, fn))
daily = lambda at, key, fn: _daily.append((at, key, fn))
manual = lambda name, fn: _manual.__setitem__(name, fn)


def load_plugins():
    for path in CFG.get("plugins", []):
        try:
            spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.setup(sys.modules[__name__])
            log("plugin loaded:", path)
        except Exception:
            log("plugin failed:", path, traceback.format_exc())


# ---------- message intake ----------

work = queue.Queue()  # the owner's intake (voice / files) and reminders: must never wait behind slow jobs
slow = queue.Queue()  # slow jobs: backup, update / group checks, plugin jobs, other people's voice notes


def worker(q=None):
    q = q or work
    while True:
        job = q.get()
        try:
            job()
        except Exception:
            log("job error", traceback.format_exc())


def slow_worker():
    worker(slow)


def intake_failed(chat, mid, reason):
    """Tell the owner (in that conversation) that a message of theirs could not be taken in, instead of losing it silently."""
    log("intake failed", chat, mid, reason)
    _ctx.chat, _ctx.src, _ctx.sync = chat, "wa", 0
    try:
        reply(("⚠️ לא הצלחתי לקלוט את ההודעה: " if LANG == "he" else "⚠️ I couldn't take in that message: ") + str(reason)[:200], quoted=mid)
    except Exception as e:
        log("intake failure notice failed", e)
    finally:
        _ctx.chat = None


APPROVE_PHRASE = "מאשר לך שליחה"


def select_draft(pending, quoted=None):
    """Which draft a text approval is for. pending = [(draft id, card message id)], quoted = the message the owner replied to.
    Returns (draft id or None, reason): a reply on a card approves that card; without a reply only a single pending draft."""
    if quoted:
        for did, card in pending:
            if card and card == quoted:
                return did, "quoted"
        return None, "not_a_card"
    if len(pending) == 1:
        return pending[0][0], "only"
    return None, ("none" if not pending else "several")


def quoted_id_of(chat, mid):
    """The message the owner's message replied to, if the bridge records it (messages.quoted_id); None otherwise."""
    try:
        with mdb() as db:
            if "quoted_id" not in [r[1] for r in db.execute("PRAGMA table_info(messages)")]:
                return None
            r = db.execute("SELECT quoted_id FROM messages WHERE id = ? AND chat_jid = ?", (mid, chat)).fetchone()
        return (r[0] or None) if r else None
    except Exception as e:
        log("quoted id lookup failed", e)
        return None


def approve_by_text(chat, mid, content):
    """The owner wrote "מאשר לך שליחה" in a control group: approve exactly one pending draft (one approval = one message)
    through the bridge with the owner-only token. No Claude run. Returns the rest of the message (to answer as a command)."""
    with mdb() as db:
        pending = db.execute("SELECT id, coalesce(card_id, '') FROM drafts WHERE status = 'pending' ORDER BY id").fetchall()
    did, why = select_draft(pending, quoted_id_of(chat, mid))
    _ctx.chat, _ctx.src, _ctx.sync = chat, "wa", 0
    try:
        if did is None:
            reply({"none": "אין טיוטה ממתינה לאישור.",
                   "several": f"יש {len(pending)} טיוטות ממתינות — הגב \"{APPROVE_PHRASE}\" בציטוט על הכרטיס הנכון, או ✅ עליו.",
                   "not_a_card": "ההודעה שציטטת אינה כרטיס טיוטה ממתין — לא נשלח כלום."}[why], quoted=mid)
        else:
            res = bridge("/draft_decide", {"id": int(did), "approve": True}, timeout=30, tok=owner_token())
            if res.get("success") is False:
                reply(f"⚠️ האישור לטיוטה #{did} נכשל: {res.get('error') or res.get('message') or 'שגיאה'}", quoted=mid)
            else:
                react(mid, "✅")  # the bridge itself posts "✅ נשלח ל…" (or the failure) when the send is done
        log("text approval", chat, mid, why, did)
    finally:
        _ctx.chat = None
    return content.replace(APPROVE_PHRASE, "").strip(" \n.,!-")


def handle(row):
    mid, cj, sender, ts, content, me, mtype = row
    content = (content or "").strip()
    if cj != LOCAL and cj in load_groups() and me:  # the owner's own message in a connected group (others' messages stay plain information)
        if content.startswith(CFG["reply_prefix"].strip()):
            return  # our own reply
        try:
            kv_set(K("last_owner_msg", cj), json.dumps({"ts": ts[:19], "text": content[:300] or f"[{mtype}]"}, ensure_ascii=False))
        except Exception as e:
            log("last_owner_msg failed", e)
        if APPROVE_PHRASE in content and mtype != "audio":
            try:
                content = approve_by_text(cj, mid, content)
            except Exception as e:
                intake_failed(cj, mid, f"האישור נכשל ({e})")
                return
            if len(content) < 4:
                return
        if mtype == "audio" and voice_enabled():
            def voice_cmd():
                try:
                    text = transcript_of(cj, mid, sender, ts)
                except Exception as e:
                    return intake_failed(cj, mid, f"התמלול נכשל ({e})")
                if not text:
                    return intake_failed(cj, mid, "התמלול ריק או שההקלטה לא ירדה")
                try:
                    enqueue_command(mid, f"{T['voice']} {text}", ts[:16], cj)
                except Exception as e:
                    intake_failed(cj, mid, e)
            work.put(voice_cmd)
        elif mtype in ("image", "document", "video"):
            def file_cmd():
                try:
                    p = media_path(cj, mid)
                    enqueue_command(mid, (content + "\n" if content else "") + T["attached"].format(path=p or T["download_failed"]), ts[:16], cj)
                except Exception as e:
                    intake_failed(cj, mid, e)
            work.put(file_cmd)
        elif content:
            try:
                enqueue_command(mid, content, ts[:16], cj)
            except Exception as e:
                intake_failed(cj, mid, e)
        return
    if mtype == "audio" and CFG.get("transcribe_all_chats") and voice_enabled():
        slow.put(lambda: transcript_of(cj, mid, sender, ts))
    for hook in _hooks:
        try:
            hook(row)
        except Exception:
            log("plugin hook error", traceback.format_exc())


_wake = threading.Event()


def event_listener():
    """Push from the bridge: wakes the sweep the moment a message is stored."""
    while True:
        try:
            req = urllib.request.Request(CFG["bridge_url"] + "/events", headers={"Authorization": "Bearer " + token()})
            with urllib.request.urlopen(req, timeout=90) as resp:
                kv_set("events_connected", dt.datetime.now().isoformat())
                for line in resp:
                    if line.startswith(b"data:"):
                        _wake.set()
        except Exception:
            time.sleep(5)


def sweep_once(cursor, on_row=None):
    """Handle messages stored after `cursor` (rowid = insertion order, so late-arriving older messages are never
    skipped); `handled` makes every message act exactly once. Returns the new cursor."""
    with mdb() as db:
        rows = db.execute("SELECT rowid, id, chat_jid, sender, timestamp, content, is_from_me, media_type FROM messages "
                          "WHERE rowid > ? ORDER BY rowid", (cursor,)).fetchall()
    for r in rows:
        with adb() as db:  # mark first: a crash must never make us act on the same message twice
            fresh = db.execute("INSERT OR IGNORE INTO handled VALUES (?, ?, ?)", (r[1], r[2], dt.datetime.now().isoformat())).rowcount
        if fresh:
            try:
                (on_row or handle)(r[1:])
            except Exception as e:  # never lose an owner message silently (it is already marked handled)
                log("handle error", traceback.format_exc())
                if r[6] and r[2] != LOCAL and r[2] in load_groups():
                    intake_failed(r[2], r[1], e)
        cursor = r[0]
    if rows:
        kv_set("cursor_rowid", cursor)
    return cursor


def sweep_loop():
    cursor = int(kv_get("cursor_rowid", "0") or 0)
    if not cursor:
        with mdb() as db:
            cursor = db.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    while True:
        try:
            cursor = sweep_once(cursor)
        except Exception:
            log("sweep error", traceback.format_exc())
        _wake.wait(15)
        _wake.clear()


# ---------- watchdog ----------

def disconnect_reason(since):
    """Best guess why WhatsApp was down, from the bridge log since `since` (shown with the 'connection is back' notice)."""
    try:
        with open(os.path.join(ROOT, "whatsapp-bridge", "bridge.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return ""
    starts = len(re.findall(r"starting bridge", tail))
    if "device_removed" in tail or "logged out" in tail.lower():
        return "\nסיבה: המכשיר הוסר מ'מכשירים מקושרים' בטלפון — אם זה חוזר, בדוק שם ואל תנתק את המחשב."
    if re.search(r"(?i)(no such host|network is unreachable|i/o timeout|connection reset|10054|10060|10065)", tail[-20000:]):
        return "\nסיבה: ניתוק רשת/אינטרנט במחשב (לא חסימה). אם חוזר — לבדוק Wi-Fi/שינה."
    if starts:
        return "\nסיבה: הגשר הופעל מחדש (עדכון או הפעלה מחדש)."
    return "\nסיבה לא ידועה — הלוג לא מראה שגיאה ברורה."


def watchdog():
    """Alerts on the PC when WhatsApp stays disconnected or the device was logged out; reports recovery."""
    down_since, alerted = None, False
    limit = dt.timedelta(minutes=CFG.get("disconnect_alert_minutes", 10))
    while True:
        try:
            st = bridge("/status", timeout=10)
            ok = bool(st.get("connected"))
            if st.get("logged_in") is False and not alerted:
                notify(T["logged_out_t"], T["logged_out_b"])
                alerted = True
        except Exception:
            ok = False
        now = dt.datetime.now()
        if ok:
            if alerted and down_since:
                mins = int((now - down_since).total_seconds() // 60)
                reply(T["back"].format(mins=mins) + disconnect_reason(down_since))
            down_since, alerted = None, False
        else:
            down_since = down_since or now
            if not alerted and now - down_since > limit:
                notify(T["down_t"], T["down_b"].format(mins=int((now - down_since).total_seconds() // 60)))
                alerted = True
        time.sleep(60)


def reminders_check():
    """Fires due reminders into the control group (owner only). kind 'time': a plain reminder.
    kind 'noreply': fires only if nobody wrote in that chat since ref_ts (otherwise it is closed quietly)."""
    now = dt.datetime.now().astimezone()
    with adb() as db:
        rows = db.execute("SELECT id, kind, text, due, chat_jid, ref_ts FROM reminders WHERE status='active' AND due <= ?",
                          (now.isoformat(timespec="seconds"),)).fetchall()
    for rid, kind, text, due, chat, ref in rows:
        late = ""
        try:
            mins = int((now - dt.datetime.fromisoformat(due)).total_seconds() // 60)
            if mins > 10:
                late = f" (באיחור של {mins} דק')" if LANG == "he" else f" ({mins} min late)"
        except ValueError:
            pass
        if kind == "noreply":
            try:
                with mdb() as m:
                    n = m.execute("SELECT COUNT(*) FROM messages WHERE chat_jid=? AND is_from_me=0 AND datetime(timestamp) > datetime(?)",
                                  (chat, ref)).fetchone()[0]
            except Exception:
                n = 0
            if n:
                with adb() as db:
                    db.execute("UPDATE reminders SET status='answered', fired=? WHERE id=?", (now.isoformat(timespec="seconds"), rid))
                continue
        head = "⏰ תזכורת" if LANG == "he" else "⏰ Reminder"
        reply(f"{head} #{rid}{late}: {text}")
        with adb() as db:
            db.execute("UPDATE reminders SET status='done', fired=? WHERE id=?", (now.isoformat(timespec="seconds"), rid))


def scheduler():
    while True:
        now = dt.datetime.now()
        today = now.date().isoformat()
        try:
            kv_set("heartbeat", now.isoformat())
            for at, key, fn in [(CFG["backup_time"], "backup", backup), (CFG.get("update_check_time", "09:00"), "update_check", update_check), (CFG.get("group_check_time", "10:00"), "group_check", group_check)] + _daily:
                if now.strftime("%H:%M") >= at and kv_get(f"day:{key}") != today:
                    kv_set(f"day:{key}", today)
                    slow.put(fn)  # backup / checks / plugin reports never delay the owner's voice notes or reminders
            if kv_get("at:reminders") != now.strftime("%Y%m%d%H%M"):
                kv_set("at:reminders", now.strftime("%Y%m%d%H%M"))
                work.put(reminders_check)
            for minutes, key, fn in _periodic:
                last = kv_get(f"at:{key}")
                if not last or now - dt.datetime.fromisoformat(last) > dt.timedelta(minutes=minutes):
                    kv_set(f"at:{key}", now.isoformat())
                    slow.put(fn)  # plugin jobs (e.g. extracting requests with Claude) run off the owner's fast queue
        except Exception:
            log("scheduler error", traceback.format_exc())
        time.sleep(30)


if __name__ == "__main__":
    for d in (OUTBOX, GROUPS_DIR, INBOX, LOCAL_FILES):
        os.makedirs(d, exist_ok=True)
    adb().close()
    load_plugins()
    if len(sys.argv) > 1:  # manual runs: agent.py backup | <plugin job>
        _manual[sys.argv[1]]()
        sys.exit()
    log("agent starting")
    if os.path.exists(RESTART_FILE):  # a request left over from before this start is already fulfilled
        os.remove(RESTART_FILE)
    threading.Thread(target=report_restart, daemon=True).start()
    try:
        cut = mark_interrupted()  # synchronous, before the dispatcher can mark anything 'running'
        if cut:
            log(f"{len(cut)} command(s) were cut off by the restart: owner told, not re-run")
            threading.Thread(target=notify_interrupted, args=(cut,), daemon=True).start()
    except Exception:
        log("mark_interrupted failed", traceback.format_exc())
    for target in [worker, slow_worker, command_worker, scheduler, event_listener, watchdog] + _threads:
        threading.Thread(target=target, daemon=True).start()
    sys.path.insert(0, ROOT)
    from dash import server as dash_server  # control panel, also on http://127.0.0.1:8770
    threading.Thread(target=dash_server.serve, args=(8770,), daemon=True).start()
    sweep_loop()
