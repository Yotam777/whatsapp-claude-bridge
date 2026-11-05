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
        "budget_stop": "💸 הגעתי לתקרת העלות היומית (${spent:.2f} מתוך ${budget:.0f}). כדי להמשיך בכל זאת — התחל את ההודעה ב-!",
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
        "prompt": "[הודעה מ{owner} בקבוצת הבקרה בווצאפ, {when}]\n{text}\n\n"
                  "ענה בשפה של {owner}, קצר ולעניין. התשובה שלך תישלח אוטומטית לקבוצה — אל תשלח אותה בעצמך. "
                  "אם צריך להחזיר קובץ (אקסל, תמונה, PDF) — שמור אותו בתיקייה outbox והוא יישלח אחרי התשובה.",
    },
    "en": {
        "budget_stop": "💸 Daily cost cap reached (${spent:.2f} of ${budget:.0f}). Start the message with ! to continue anyway.",
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
        "prompt": "[Message from {owner} in the WhatsApp control group, {when}]\n{text}\n\n"
                  "Answer briefly in {owner}'s language. Your answer is posted to the group automatically — do not send it yourself. "
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


def adb():
    db = sqlite3.connect(AGENT_DB, timeout=30)
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
    """)
    return db


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


def bridge(path, body=None, timeout=180):
    """Call the bridge API (token-protected). Errors that carry a JSON body (drafts, refusals) are returned as data."""
    req = urllib.request.Request(CFG["bridge_url"] + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token()})
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
        res = bridge("/send", body)
        ok, detail = res.get("success", False), res.get("message", "")
    except Exception as e:
        ok, detail = False, str(e)
    with adb() as db:
        db.execute("INSERT INTO sent VALUES (?, ?, ?, ?, ?)", (dt.datetime.now().isoformat(), recipient, text[:500], ok, detail))
    log("send", recipient, ok, detail[:120])
    return ok


def reply(text, quoted=None):
    kv_set("last_tool_msg", json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "text": text.strip()[:300]}, ensure_ascii=False))
    send(CFG["claude_group"], CFG["reply_prefix"] + text.strip(), quoted=quoted)


def react(msg_id, emoji):
    try:
        bridge("/react", {"chat_jid": CFG["claude_group"], "message_id": msg_id, "sender": "", "emoji": emoji}, timeout=20)
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

def _run_claude(prompt, model, resume, timeout):
    cmd = [shutil.which("claude") or "claude", "-p", prompt, "--output-format", "json", "--permission-mode", "auto"]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    t0 = time.time()
    out = subprocess.run(cmd, cwd=HOME, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    res = json.loads(out.stdout or "{}")
    usage = res.get("usage", {})
    with adb() as db:
        db.execute("INSERT INTO claude_runs VALUES (?, ?, ?, ?, ?, ?, ?)", (dt.datetime.now().isoformat(), res.get("session_id"),
                   res.get("total_cost_usd", 0), usage.get("input_tokens", 0), usage.get("output_tokens", 0), time.time() - t0,
                   0 if res.get("is_error") else 1))
    return res


def write_handoff(old_sid):
    """Before a session is rotated, ask it for a short hand-off so the next session keeps the thread."""
    try:
        res = _run_claude(T["handoff_prompt"].format(owner=OWNER), None, old_sid, 300)
        if res.get("result"):
            with open(os.path.join(HOME, "handoff.md"), "w", encoding="utf-8") as f:
                f.write(f"{T['handoff_title']} ({dt.datetime.now():%d.%m.%Y %H:%M})\n\n{res['result'].strip()}\n")
            log("handoff written")
    except Exception as e:
        log("handoff failed", e)


def claude(prompt, model=None, session=True, timeout=900):
    """Run Claude Code headless in HOME. The conversation session is resumed until it is rotated (daily, with hand-off)."""
    sid = kv_get("session_id") if session else None
    started = kv_get("session_started")
    if sid and started and dt.datetime.now() - dt.datetime.fromisoformat(started) > dt.timedelta(hours=CFG["session_rotate_hours"]):
        write_handoff(sid)
        sid = None
    try:
        res = _run_claude(prompt, model, sid, timeout)
    except Exception as e:
        log("claude failed", e)
        return None
    if session and res.get("session_id"):
        if res["session_id"] != kv_get("session_id"):
            kv_set("session_started", dt.datetime.now().isoformat())
        kv_set("session_id", res["session_id"])
    return res.get("result")


def spent_today():
    with adb() as db:
        return db.execute("SELECT coalesce(sum(cost),0) FROM claude_runs WHERE substr(at,1,10) = ?", (dt.date.today().isoformat(),)).fetchone()[0]


# The owner's questions are stored before they are answered and closed only after the reply went out,
# so a restart in the middle picks them up again instead of losing them.
_command_signal = threading.Event()


def enqueue_command(msg_id, text, when):
    with adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at) VALUES (?, ?, ?)", (msg_id, text, when))
    react(msg_id, "👀")  # immediate "got it"
    _command_signal.set()


def send_outbox(since):
    """Files Claude saved in home/outbox during the answer are sent to the owner, then archived."""
    if not os.path.isdir(OUTBOX):
        return
    done = os.path.join(OUTBOX, "sent")
    os.makedirs(done, exist_ok=True)
    for f in sorted(os.listdir(OUTBOX)):
        p = os.path.join(OUTBOX, f)
        if os.path.isfile(p) and os.path.getmtime(p) >= since - 5:
            send(CFG["claude_group"], CFG["reply_prefix"] + "📎 " + f, media=p)
            shutil.move(p, os.path.join(done, f"{dt.datetime.now():%Y%m%d_%H%M%S}_{f}"))


def command_worker():
    while True:
        with adb() as db:
            row = db.execute("SELECT id, msg_id, text, at FROM commands WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
        if not row:
            _command_signal.wait(30)
            _command_signal.clear()
            continue
        cid, mid, text, when = row
        try:
            budget = float(CFG.get("daily_budget_usd", 5))
            spent = spent_today()
            override = text.lstrip().startswith("!")
            if spent >= budget and not override:
                reply(T["budget_stop"].format(spent=spent, budget=budget))
            else:
                if spent >= budget * 0.8 and kv_get("budget_warned") != dt.date.today().isoformat():
                    kv_set("budget_warned", dt.date.today().isoformat())
                    reply(T["budget_warn"].format(spent=spent, budget=budget))
                prompt = T["prompt"].format(owner=OWNER, when=when, text=text.lstrip("!").strip())
                light = len(text) < 60 and T["attached"].split("{")[0] not in text
                model = (CFG.get("light_model") if light else None) or CFG.get("claude_model") or None
                started = time.time()
                progress = threading.Timer(45, lambda: reply(T["working"]))
                progress.start()
                try:
                    answer = claude(prompt, model=model)
                finally:
                    progress.cancel()
                reply(answer or T["failed"])
                send_outbox(started)
                react(mid, "✅")
        except Exception:
            log("command error", traceback.format_exc())
        with adb() as db:
            db.execute("UPDATE commands SET status = 'done', answered = ? WHERE id = ?", (dt.datetime.now().isoformat(), cid))


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
    json.dump(res, open(os.path.join(HERE, "update_check.json"), "w", encoding="utf-8"), ensure_ascii=False)
    if res.get("available") and kv_get("update_notified") != res["latest"]:
        kv_set("update_notified", res["latest"])
        reply(T["update"].format(latest=res["latest"], current=res["current"], notes=(res.get("notes") or "")[:800]))


# ---------- plugins ----------
# A plugin is a Python file with setup(agent). It may register:
#   agent.on_message(fn(row))                    called for every new message (row as in sweep())
#   agent.every(minutes, key, fn)                periodic job
#   agent.daily("HH:MM", key, fn)                once a day at/after that time
#   agent.background(fn)                         long-running thread
#   agent.manual(name, fn)                       `agent.py <name>` on the command line

_hooks, _periodic, _daily, _threads, _manual = [], [], [], [], {"backup": backup, "update_check": update_check}
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

work = queue.Queue()  # slow jobs (transcription, plugin work) so intake never blocks


def worker():
    while True:
        job = work.get()
        try:
            job()
        except Exception:
            log("job error", traceback.format_exc())


def handle(row):
    mid, cj, sender, ts, content, me, mtype = row
    content = (content or "").strip()
    if cj == CFG["claude_group"] and me:
        if content.startswith(CFG["reply_prefix"].strip()):
            return  # our own reply
        kv_set("last_owner_msg", json.dumps({"ts": ts[:19], "text": content[:300] or f"[{mtype}]"}, ensure_ascii=False))
        if mtype == "audio" and voice_enabled():
            def voice_cmd():
                text = transcript_of(cj, mid, sender, ts)
                if text:
                    enqueue_command(mid, f"{T['voice']} {text}", ts[:16])
            work.put(voice_cmd)
        elif mtype in ("image", "document", "video"):
            def file_cmd():
                p = media_path(cj, mid)
                enqueue_command(mid, (content + "\n" if content else "") + T["attached"].format(path=p or T["download_failed"]), ts[:16])
            work.put(file_cmd)
        elif content:
            enqueue_command(mid, content, ts[:16])
        return
    if mtype == "audio" and CFG.get("transcribe_all_chats") and voice_enabled():
        work.put(lambda: transcript_of(cj, mid, sender, ts))
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
            (on_row or handle)(r[1:])
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
                reply(T["back"].format(mins=mins))
            down_since, alerted = None, False
        else:
            down_since = down_since or now
            if not alerted and now - down_since > limit:
                notify(T["down_t"], T["down_b"].format(mins=int((now - down_since).total_seconds() // 60)))
                alerted = True
        time.sleep(60)


def scheduler():
    while True:
        now = dt.datetime.now()
        today = now.date().isoformat()
        try:
            kv_set("heartbeat", now.isoformat())
            for at, key, fn in [(CFG["backup_time"], "backup", backup), (CFG.get("update_check_time", "09:00"), "update_check", update_check)] + _daily:
                if now.strftime("%H:%M") >= at and kv_get(f"day:{key}") != today:
                    kv_set(f"day:{key}", today)
                    work.put(fn)
            for minutes, key, fn in _periodic:
                last = kv_get(f"at:{key}")
                if not last or now - dt.datetime.fromisoformat(last) > dt.timedelta(minutes=minutes):
                    kv_set(f"at:{key}", now.isoformat())
                    work.put(fn)
        except Exception:
            log("scheduler error", traceback.format_exc())
        time.sleep(30)


if __name__ == "__main__":
    os.makedirs(OUTBOX, exist_ok=True)
    adb().close()
    load_plugins()
    if len(sys.argv) > 1:  # manual runs: agent.py backup | <plugin job>
        _manual[sys.argv[1]]()
        sys.exit()
    log("agent starting")
    for target in [worker, command_worker, scheduler, event_listener, watchdog] + _threads:
        threading.Thread(target=target, daemon=True).start()
    sys.path.insert(0, ROOT)
    from dash import server as dash_server  # control panel, also on http://127.0.0.1:8770
    threading.Thread(target=dash_server.serve, args=(8770,), daemon=True).start()
    sweep_loop()
