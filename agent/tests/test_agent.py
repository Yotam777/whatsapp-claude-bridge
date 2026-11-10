"""Agent tests that need no WhatsApp account: a temporary store + agent database."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    db = sqlite3.connect(store / "messages.db")
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN, "
               "media_type TEXT, filename TEXT, PRIMARY KEY (id, chat_jid))")
    db.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.commit()
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    cfg.update({"store_dir": str(store), "claude_group": "1203630000@g.us", "transcribe": False, "plugins": []})
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("WA_AGENT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("WA_AGENT_DB", str(tmp_path / "agent.db"))
    sys.path.insert(0, HERE)
    mod = importlib.reload(importlib.import_module("agent"))
    mod.LOG = str(tmp_path / "agent.log")
    # never touch the real installation's files from a test (model choice, groups, restart / stop requests)
    for name, fname in (("MODEL_FILE", "model_override.json"),
                        ("RESTART_FILE", "restart.request"), ("STOP_FILE", "stop.request")):
        monkeypatch.setattr(mod, name, str(tmp_path / fname))
    mod.adb().close()
    return mod, db


def add(db, mid, ts, content="hi", chat="972500000001@s.whatsapp.net"):
    db.execute("INSERT INTO messages VALUES (?, ?, 'x', ?, ?, 0, '', '')", (mid, chat, content, ts))
    db.commit()


def test_every_message_handled_exactly_once(agent):
    mod, db = agent
    seen = []
    add(db, "A", "2026-11-06 10:00:00+02:00")
    add(db, "B", "2026-11-06 10:01:00+02:00")
    cur = mod.sweep_once(0, seen.append)
    cur = mod.sweep_once(cur, seen.append)
    assert [r[0] for r in seen] == ["A", "B"]
    # a message stored later but carrying an OLDER timestamp (offline sync) must still be picked up
    add(db, "C", "2026-11-06 09:00:00+02:00")
    mod.sweep_once(cur, seen.append)
    assert [r[0] for r in seen] == ["A", "B", "C"]
    # a replaced row (new rowid) is not handled again
    db.execute("INSERT OR REPLACE INTO messages VALUES ('A', '972500000001@s.whatsapp.net', 'x', 'hi', '2026-11-06 10:00:00+02:00', 0, '', '')")
    db.commit()
    mod.sweep_once(0, seen.append)
    assert [r[0] for r in seen] == ["A", "B", "C"]


def test_owner_commands_are_durable(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "react", lambda *a: None)
    mod.enqueue_command("M1", "hello", "2026-11-06 10:00")
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE msg_id = 'M1'").fetchone()[0] == "pending"


def test_strings_exist_in_both_languages(agent):
    mod, _ = agent
    src = open(os.path.join(HERE, "agent.py"), encoding="utf-8").read()
    he = src[src.index('"he": {'):src.index('"en": {')]
    en = src[src.index('"en": {'):src.index("}[LANG]")]
    keys = lambda block: {line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"') and '":' in line}
    assert keys(he) - {"he"} == keys(en) - {"en"}


def test_italic_marks_tool_messages(agent):
    mod, _ = agent
    assert mod.italic("שלום\n\n  • נקודה ") == "_שלום_\n\n  _• נקודה_"
    assert mod.italic("https://x.com/a_b") == "https://x.com/a_b"  # underscores: left as is
    assert mod.italic("```code```") == "```code```"


def test_run_cost_from_cumulative_session_figure(agent):
    mod, _ = agent
    assert mod.run_cost(0.4, None) == 0.4             # first run of a session
    assert round(mod.run_cost(8.65, 8.63), 2) == 0.02  # resumed session: only this run's share
    assert mod.run_cost(0.3, 8.0) == 0.3               # lower figure = a new count


def test_old_cumulative_rows_are_converted(agent, tmp_path):
    mod, _ = agent
    with mod.adb() as db:
        db.execute("UPDATE claude_runs SET cum = NULL")
        for at, cost in (("2026-11-07T10:00", 5.0), ("2026-11-07T10:05", 5.5), ("2026-11-07T10:10", 6.0)):
            db.execute("INSERT INTO claude_runs (at, session, cost, cum) VALUES (?, 's1', ?, NULL)", (at, cost))
    mod._runs_migrated = False
    with mod.adb() as db:
        costs = [round(r[0], 2) for r in db.execute("SELECT cost FROM claude_runs WHERE session = 's1' ORDER BY at")]
    assert costs == [5.0, 0.5, 0.5]


def test_restart_command_and_strong_model_prefix(agent):
    mod, _ = agent
    assert mod.owner_command("/הפעל מחדש") and mod.RESTART_REQ.is_set()
    mod.RESTART_REQ.clear()
    mod.CFG["deep_model"] = "opus"
    assert mod.pick_model("!! תבדוק לעומק") == "opus"
    assert mod.pick_model("!!! מה המצב") != "opus" or mod.get_override() == "opus"


# ---------- the conversation: session / model / restart behaviour ----------


@pytest.fixture()
def _run_worker(mod, monkeypatch, answer="התשובה"):
    """Start the command worker with Claude and the bridge faked; returns the list of (chat, text) sent to WhatsApp and the claude calls."""
    sent, calls = [], []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {})
    monkeypatch.setattr(mod, "claude", lambda prompt, model=None, session=True, timeout=900, on_stage=None, chat=None, **k: calls.append((chat, model, prompt)) or answer)
    import threading
    threading.Thread(target=mod.command_worker, daemon=True).start()
    return sent, calls


def _wait_done(mod, n=1, secs=10):
    import time
    end = time.time() + secs
    while time.time() < end:
        with mod.adb() as db:
            if db.execute("SELECT count(*) FROM commands WHERE status = 'done'").fetchone()[0] >= n:
                return True
        time.sleep(0.2)
    return False


# ---------- review fixes 08.11: interrupted commands, dev-work on raw text, sticky model, total cap, text approval ----------

def test_interrupted_command_is_not_rerun(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "wait_bridge", lambda *a, **k: True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('r1', 'עבודה ארוכה', '2026-11-08 07:00', ?, 'wa', 0, 'running')", (mod.PRIMARY,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('p1', 'עוד לא התחיל', '2026-11-08 07:01', ?, 'wa', 0, 'pending')", (mod.PRIMARY,))
    rows = mod.mark_interrupted()
    assert [r[1] for r in rows] == ["r1"]
    mod.notify_interrupted(rows)
    with mod.adb() as db:
        st = dict(db.execute("SELECT msg_id, status FROM commands").fetchall())
    assert st == {"r1": "interrupted", "p1": "pending"}, "running → interrupted (not re-run); pending still answered"
    assert any(c == mod.PRIMARY and "נקטעה" in t for c, t in sent)
    row = mod.claim_next(mod.PRIMARY)
    assert row[1] == "p1"
    with mod.adb() as db:
        assert db.execute("SELECT status, attempts FROM commands WHERE msg_id = 'p1'").fetchone() == ("running", 1)
    assert mod.claim_next(mod.PRIMARY) is None


def test_dev_work_uses_the_owners_own_words(agent):
    mod, _ = agent
    wrapped = mod.T["prompt"].format(owner="יקיר", when="x", text="מה עם הנעליים של דני", where='מהמחשב (לוח הבקרה, "4M")', dest="לחלון")
    assert mod.is_dev_work(wrapped), "the wrapper itself mentions the dashboard"
    attached = "תסדר את הטופס\n" + mod.T["attached"].format(path="C:/Users/me/whatsapp-mcp/whatsapp-bridge/store/x.jpg")
    assert not mod.is_dev_work(mod.raw_text(attached)), "an attachment path is not dev work"
    assert mod.is_dev_work(mod.raw_text("תתקן את הגשר"))


def test_model_is_sticky_within_a_session(agent):
    mod, _ = agent
    mod.CFG.update({"deep_model": "opus", "light_model": "sonnet", "claude_model": "sonnet", "fresh_sessions": False})
    chat = mod.PRIMARY
    # brand-new session: deep words may start it on the deep model
    assert mod.pick_model_info("תכנן לי את השבוע", chat) == ("opus", "auto")
    # running session on sonnet: deep words keep sonnet (cache) and suggest !!
    mod.kv_set(mod.K("session_id", chat), "s1")
    mod.kv_set(mod.K("session_started", chat), mod.dt.datetime.now().isoformat())
    mod.kv_set(mod.K("session_model", chat), "sonnet")
    assert mod.pick_model_info("תכנן לי את השבוע", chat) == ("sonnet", "sticky_deep")
    assert mod.pick_model_info("!! תכנן לי את השבוע", chat) == ("opus", "bang"), "explicit !! still works"
    mod.set_override("haiku", None, chat)
    assert mod.pick_model_info("תכנן", chat) == ("haiku", "forced")
    mod.set_override("auto", None, chat)
    # a session that started on opus keeps opus (no switch mid-session)
    mod.kv_set(mod.K("session_model", chat), "opus")
    assert mod.pick_model_info("מה המצב", chat) == ("opus", "auto")
    # light → strong is the only automatic switch in a running session
    mod.CFG["light_model"] = "haiku"
    mod.kv_set(mod.K("session_model", chat), "haiku")
    assert mod.pick_model_info("תסכם את הדוח", chat) == ("sonnet", "auto")
    mod.kv_set(mod.K("session_model", chat), "sonnet")
    assert mod.pick_model_info("שלום", chat) == ("sonnet", "auto"), "no step down mid-session"
    # yesterday's session rotates: free choice again
    mod.kv_set(mod.K("session_started", chat), "2020-01-01T00:00:00")
    assert mod.pick_model_info("תכנן", chat) == ("opus", "auto")


def test_stop_request_targets_its_conversation(agent):
    mod, _ = agent
    assert mod.stop_applies("1203639999@g.us", "1203639999@g.us", {})
    assert not mod.stop_applies("1203639999@g.us", mod.PRIMARY, {})
    assert mod.stop_applies("", mod.PRIMARY, {"a": 1, "b": 2}), "legacy empty request stops the primary run"
    assert mod.stop_applies("", "1203639999@g.us", {"1203639999@g.us": 1}), "or the only run"
    assert not mod.stop_applies("", "1203639999@g.us", {"x": 1, "1203639999@g.us": 1})


# ---------- 12:49 approvals: limit, sign-in, reconnect, schedule, morning summary, voice ----------

def test_usage_limit_parsing(agent):
    mod, _ = agent
    now = mod.dt.datetime(2026, 11, 8, 10, 17)
    r = mod.parse_limit("You've hit your 5-hour limit · resets 11:30am (Asia/Jerusalem)", now)
    assert r == mod.dt.datetime(2026, 11, 8, 11, 30)
    assert mod.parse_limit("You've hit your limit · resets 3pm", now) == mod.dt.datetime(2026, 11, 8, 15, 0)
    assert mod.parse_limit("You've hit your weekly limit · resets 9am", now) == mod.dt.datetime(2026, 11, 9, 9, 0), "a past hour = tomorrow"
    assert mod.parse_limit("Usage limit reached", now) == now + mod.dt.timedelta(minutes=60), "no time given → +60 min"
    assert mod.parse_limit("הנה סיכום של הדוח", now) is None and mod.parse_limit("", now) is None


def test_usage_limit_parks_and_requeues_commands(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    until = mod.dt.datetime.now() + mod.dt.timedelta(minutes=30)
    mod.kv_set("limit_until", until.isoformat(timespec="seconds"))
    for i in (1, 2):
        mod.enqueue_command(f"L{i}", "מה המצב", "2026-11-08 10:20")
    called = []
    monkeypatch.setattr(mod, "claude", lambda *a, **k: called.append(1) or "x")
    for _ in (1, 2):
        mod.run_command(mod.claim_next(mod.PRIMARY))
    with mod.adb() as db:
        st = [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")]
    assert st == ["waiting_limit", "waiting_limit"] and not called, "no Claude run while the limit is on"
    assert sum("מכסת השימוש" in t for t in sent) == 1, "one notice per window, not per command"
    assert mod.release_limit_waiters() == 0, "still waiting"
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert mod.release_limit_waiters() == 2
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")] == ["pending", "pending"]


def test_limit_hit_during_a_run_parks_the_command(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    until = mod.dt.datetime.now() + mod.dt.timedelta(minutes=40)

    def hit(*a, **k):
        raise mod.LimitHit(until)
    monkeypatch.setattr(mod, "_run_claude", hit)
    mod.enqueue_command("L9", "תסכם לי את היום", "2026-11-08 10:20")
    mod.run_command(mod.claim_next(mod.PRIMARY))
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands").fetchone()[0] == "waiting_limit"
    assert any(until.strftime("%H:%M") in t for t in sent)


def test_background_runs_skip_quietly_during_the_limit(agent, monkeypatch):
    mod, _ = agent
    import pytest as _pt
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(minutes=5)).isoformat(timespec="seconds"))

    def boom(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(mod, "_run_claude", boom)
    assert mod.claude("x", session=False) is None
    with _pt.raises(mod.LimitHit):
        mod.claude("x", on_stage=lambda *a: None)


def test_signin_expiry_parsing_and_warning(agent, monkeypatch):
    mod, _ = agent
    err = "Warning: this sign-in ends between 2026-11-11 and 2026-11-14 (in 3 days at the earliest). Renewing it does not move those dates."
    assert mod.parse_signin(err) == ("2026-11-11", "2026-11-14")
    assert mod.parse_signin("nothing here") is None
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    soon = (mod.dt.date.today() + mod.dt.timedelta(days=2)).isoformat()
    mod.kv_set("signin_ends", f"{soon}|{soon}")
    mod.signin_check()
    assert sent and "claude auth login" in sent[-1]
    sent.clear()
    far = (mod.dt.date.today() + mod.dt.timedelta(days=20)).isoformat()
    mod.kv_set("signin_ends", f"{far}|{far}")
    mod.signin_check()
    assert not sent


def test_reconnect_finds_missed_owner_messages(agent, monkeypatch):
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
    now = mod.dt.datetime.now().replace(microsecond=0)
    start = now - mod.dt.timedelta(minutes=20)
    db.execute("INSERT INTO conn_log VALUES (?, 'disconnected')", ((start - mod.dt.timedelta(seconds=40)).strftime("%Y-%m-%d %H:%M:%S"),))
    db.execute("INSERT INTO conn_log VALUES (?, 'logged_out')", ((start - mod.dt.timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),))

    def ts(m):
        return (now - mod.dt.timedelta(minutes=m)).astimezone().isoformat(sep=" ", timespec="seconds")
    rows = [("O1", mod.PRIMARY, "me", "מה המצב עם הגשר", ts(15), 1, ""),       # owner, during the outage, never handled -> missed
            ("O2", mod.PRIMARY, "me", "🤖 _תשובה_", ts(10), 1, ""),     # the bot's own reply -> ignored
            ("O3", mod.PRIMARY, "x", "הודעה של מישהו אחר", ts(12), 0, ""),      # not the owner -> ignored
            ("O4", mod.PRIMARY, "me", "לפני הניתוק", ts(60), 1, ""),             # before the outage -> ignored
            ("O5", mod.PRIMARY, "me", "כבר טופלה", ts(5), 1, "")]       # already handled -> ignored
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, '')", rows)
    db.commit()
    with mod.adb() as a:
        a.execute("INSERT INTO handled VALUES ('O5', ?, 'x')", (mod.PRIMARY,))
    s, e = mod.outage_window(start, now)
    assert s < start, "the bridge's own 'disconnected' time is used when it is earlier"
    assert [r[0] for r in mod.missed_owner_messages(s, e)] == ["O1"]
    assert "QR" in mod.outage_reason(s, e)
    handled, sent = [], []
    monkeypatch.setattr(mod, "handle", lambda r: handled.append(r[0]))
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    mod.report_reconnect(start, now, False, settle=0)
    assert handled == ["O1"]
    assert sent and sent[-1][0] == mod.PRIMARY and "נמצאו 1" in sent[-1][1] and "20" in sent[-1][1]
    # a short blip with nothing missed stays quiet
    sent.clear()
    mod.report_reconnect(now - mod.dt.timedelta(seconds=50), now, False, settle=0)
    assert not sent


def test_schedule_time_parsing(agent):
    mod, _ = agent
    now = mod.dt.datetime(2026, 11, 8, 13, 0)
    d = mod.dt.datetime
    assert mod.parse_send_time("08:00", now) == d(2026, 11, 9, 8, 0), "a passed hour today = tomorrow"
    assert mod.parse_send_time("ב-17:30", now) == d(2026, 11, 8, 17, 30)
    assert mod.parse_send_time("מחר 08:00", now) == d(2026, 11, 9, 8, 0)
    assert mod.parse_send_time("שלח ב 8", now) == d(2026, 11, 9, 8, 0)
    assert mod.parse_send_time("מחר ב-9", now) == d(2026, 11, 9, 9, 0)
    assert mod.parse_send_time("15:00.", now) == d(2026, 11, 8, 15, 0)
    for no in ("תשנה את השעה ל-08:00 בטיוטה", "25:00", "שלום", "", "8 קסדות"):
        assert mod.parse_send_time(no, now) is None, no


def test_morning_summary_extract(agent):
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.execute("INSERT INTO chats VALUES ('972500000002@s.whatsapp.net', 'דנה', NULL)")
    db.execute("INSERT INTO chats VALUES ('972500000003@s.whatsapp.net', 'רון', NULL)")
    now = mod.dt.datetime.now().astimezone().replace(microsecond=0)

    def ts(m):
        return (now - mod.dt.timedelta(minutes=m)).isoformat(sep=" ")
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, '')", [
        ("a", "972500000002@s.whatsapp.net", "972500000002", "אתה מגיע מחר?", ts(50), 0, ""),   # Dana waits for an answer
        ("b", "972500000003@s.whatsapp.net", "972500000003", "תודה", ts(40), 0, ""),
        ("c", "972500000003@s.whatsapp.net", "me", "בשמחה", ts(30), 1, ""),                      # Ron was answered
        ("d", mod.PRIMARY, "me", "הודעה בקבוצת הבקרה", ts(20), 1, ""),                            # control group -> excluded
        ("e", "120363777@g.us", "972500000004", "", ts(10), 0, "sticker"),                         # sticker-only chat -> skipped
    ])
    db.commit()
    lines, waiting = mod.summary_extract(now - mod.dt.timedelta(hours=2), now)
    text = "\n".join(lines)
    assert "## דנה" in text and "## רון" in text and "קבוצת הבקרה" not in text and "120363777" not in text
    assert [w[0] for w in waiting] == ["דנה"]


def test_tts_text_cleaning(agent):
    mod, _ = agent
    t = mod.tts_clean("*סיכום:* _הכול_ תקין ✅🙂\nראה https://example.com/x\n`code`")
    assert "*" not in t and "_" not in t and "✅" not in t and "http" not in t and "code" not in t
    assert "סיכום" in t and "קישור" in t
    c = mod.tts_clean("משפט ארוך שחוזר על עצמו. " * 100)
    assert len(c) < 900 and c.endswith("ההמשך בהודעה הכתובה.")


def test_command_line_is_read_only(agent, monkeypatch):
    mod, _ = agent
    cmd = mod.claude_cmd("sonnet", "sid1", append="x")
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "Bash" not in tools and "Edit" not in tools and "Write" not in tools and "WebFetch" not in tools
    assert "--strict-mcp-config" in cmd
    mcp = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["whatsapp"]
    assert mcp["args"][-1].endswith("main.py")
    assert "Bash" in cmd[cmd.index("--disallowedTools") + 1]
    assert cmd[-2:] == ["--resume", "sid1"]
    # a restricted classification run stays restricted (no MCP at all)
    r = mod.claude_cmd(None, None, tools="")
    assert r[r.index("--tools") + 1] == "" and "--mcp-config" not in r


def test_prompt_has_no_outbox(agent, monkeypatch):
    mod, _ = agent
    assert "outbox" not in mod.T["prompt"] and "{text}" in mod.T["prompt"]


def test_whisper_uses_gpu_and_falls_back_to_cpu(agent, monkeypatch):
    import types
    mod, _ = agent
    made = []

    class WM:
        def __init__(self, name, device, compute_type):
            if device == "cuda" and fail["gpu"]:
                raise RuntimeError("cudnn missing")
            made.append((device, compute_type))
    fail = {"gpu": False}
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(mod.CFG, "whisper_model", "m")
    assert mod._load_whisper()[1] == "cuda" and made[-1] == ("cuda", "float16")
    fail["gpu"] = True
    assert mod._load_whisper()[1] == "cpu" and made[-1] == ("cpu", "int8")
    monkeypatch.setitem(mod.CFG, "whisper_device", "cpu")
    fail["gpu"] = False
    assert mod._load_whisper()[1] == "cpu"


def test_transcript_posted_to_control_group(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda r, t, **k: sent.append((r, t)) or True)
    monkeypatch.setattr(mod, "transcript_of", lambda *a: "תביא ביצים")
    monkeypatch.setattr(mod, "voice_label", lambda c, s: "מיכל")
    monkeypatch.setitem(mod.CFG, "post_transcripts", True)
    mod.transcribe_and_post("972500000001@s.whatsapp.net", "v1", "x", "2026-11-09 00:20:00+02:00")
    assert sent == [(mod.PRIMARY, mod.CFG["reply_prefix"] + "🎙️ *מיכל* (00:20):\nתביא ביצים")]
    # the owner's own voice notes, and the setting off, post nothing (still transcribed)
    mod.transcribe_and_post("972500000001@s.whatsapp.net", "v2", "x", "2026-11-09 00:21:00+02:00", me=True)
    monkeypatch.setitem(mod.CFG, "post_transcripts", False)
    mod.transcribe_and_post("972500000001@s.whatsapp.net", "v3", "x", "2026-11-09 00:22:00+02:00")
    assert len(sent) == 1


def test_backfill_transcribes_only_missing_recent_notes(agent, monkeypatch):
    import datetime as _dt
    mod, db = agent
    now = _dt.datetime.now()
    recent, old = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), (now - _dt.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    for mid, ts in (("r1", recent), ("r2", recent), ("o1", old)):
        db.execute("INSERT INTO messages VALUES (?, 'c@s.whatsapp.net', 'x', '', ?, 0, 'audio', '')", (mid, ts))
    db.commit()
    with mod.adb() as a:
        a.execute("INSERT INTO transcripts VALUES ('r2', 'c@s.whatsapp.net', 'x', '', 'done', 'm')")
    done = []
    monkeypatch.setattr(mod, "voice_enabled", lambda: True)
    monkeypatch.setattr(mod, "transcript_of", lambda c, m, s, t: done.append(m))
    mod.backfill_transcripts()
    assert done == ["r1"]


def test_bridge_retries_dropped_connection_only_when_safe(agent, monkeypatch):
    import io
    mod, _ = agent
    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append(req.full_url.rsplit("/", 1)[-1])
            if len(calls) % 2 == 1:
                raise ConnectionResetError(10054, "forcibly closed")
            return io.BytesIO(b'{"ok": true}')
    monkeypatch.setattr(mod, "LOCAL_HTTP", Opener())
    monkeypatch.setattr(mod, "token", lambda: "t")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert mod.bridge("/react", {}) == {"ok": True} and calls == ["react", "react"]
    calls.clear()
    with pytest.raises(ConnectionResetError):
        mod.bridge("/send", {})
    assert calls == ["send"]  # never repeated: it may already have gone out


def _contacts_db(mod, tmp_rows):
    w = sqlite3.connect(mod.WA_DB)
    w.execute("CREATE TABLE IF NOT EXISTS whatsmeow_contacts (our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT, "
              "push_name TEXT, business_name TEXT)")
    w.execute("CREATE TABLE IF NOT EXISTS whatsmeow_lid_map (lid TEXT, pn TEXT)")
    for r in tmp_rows:
        w.execute("INSERT INTO whatsmeow_contacts VALUES ('me', ?, '', ?, ?, '')", r)
    w.execute("INSERT INTO whatsmeow_lid_map VALUES ('111222', '972501234567')")
    w.commit()
    w.close()


def test_transcribe_command_and_in_chat_reply(agent, monkeypatch):
    mod, _ = agent
    _contacts_db(mod, [("972501234567@s.whatsapp.net", "שון שלי", "Shon"), ("972509999999@s.whatsapp.net", "", "שוני")])
    policy = {"allow": ["972500000077"]}
    sent = []

    def fake_bridge(path, body=None, timeout=180, tok=None):
        if path == "/policy" and body is not None:
            policy.update(body)
        return dict(policy)
    monkeypatch.setattr(mod, "bridge", fake_bridge)
    monkeypatch.setattr(mod, "send", lambda r, t, media=None, quoted=None: sent.append((r, t, quoted)) or True)
    out = mod.owner_command("/תמלול הוסף שון")
    assert "שון שלי" in out and "תשלח" in out
    # both keys of the chat are allowed, and the earlier approved recipient is kept
    assert set(policy["allow"]) == {"972500000077", "972501234567@s.whatsapp.net", "111222@lid"}
    assert "שון שלי" in mod.owner_command("/תמלול")
    monkeypatch.setattr(mod, "transcript_of", lambda *a: "תביא ביצים")
    monkeypatch.setattr(mod, "voice_label", lambda c, s: "שון שלי")
    monkeypatch.setitem(mod.CFG, "post_transcripts", True)
    # MY voice note to the chosen contact (chat keyed by LID): its transcript goes right under it, in that chat only
    mod.transcribe_and_post("111222@lid", "v9", "me", "2026-11-09 18:00:00+02:00", me=True)
    assert sent == [("111222@lid", "📝 תביא ביצים", "v9")]
    # THEIR voice note: the control group only (nothing in their chat)
    sent.clear()
    mod.transcribe_and_post("111222@lid", "v10", "111222@lid", "2026-11-09 18:01:00+02:00")
    assert [r for r, _, _ in sent] == [mod.PRIMARY]
    # my voice note to someone not on the list: nothing at all
    sent.clear()
    mod.transcribe_and_post("972509999999@s.whatsapp.net", "v11", "me", "2026-11-09 18:02:00+02:00", me=True)
    assert sent == []
    # removing takes both keys off the allow list again
    assert "הוסר" in mod.owner_command("/תמלול הסר שון")
    assert policy["allow"] == ["972500000077"]


def test_send_reset_after_bridge_sent_is_not_resent(agent, monkeypatch):
    import datetime as _dt
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS send_log (id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT, message_id TEXT, "
               "ok BOOLEAN, detail TEXT, timestamp TIMESTAMP)")
    db.commit()
    calls = []

    def fake_bridge(path, body=None, timeout=180, tok=None):
        calls.append(path)
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.123456+02:00")
        db.execute("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES (?, 'M1', 1, '', ?)",
                   (body["recipient"], now))
        db.commit()
        raise ConnectionResetError(10054, "forcibly closed")
    monkeypatch.setattr(mod, "bridge", fake_bridge)
    assert mod.send("972500000001@s.whatsapp.net", "hi") is True
    assert calls == ["/send"]  # confirmed in the send log: not sent twice


def test_media_path_waits_for_the_auto_saved_file(agent, monkeypatch):
    import threading as _th
    mod, db = agent
    db.execute("INSERT INTO messages VALUES ('a1', 'c@s.whatsapp.net', 'x', '', '2026-11-09 18:39:10+02:00', 1, 'audio', 'audio_a1.ogg')")
    db.commit()
    d = os.path.join(mod.STORE, "c@s.whatsapp.net")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no /download needed")))
    _th.Timer(1.2, lambda: open(os.path.join(d, "audio_a1.ogg"), "wb").write(b"ogg")).start()
    assert mod.media_path("c@s.whatsapp.net", "a1", wait=5).endswith("audio_a1.ogg")


def test_whisper_unloaded_when_idle(agent, monkeypatch):
    mod, _ = agent
    mod._whisper, mod._whisper_used = object(), 0.0
    monkeypatch.setitem(mod.CFG, "whisper_idle_unload_minutes", 10)
    ticks = []

    def fake_sleep(s):
        ticks.append(s)
        if len(ticks) > 1:
            raise SystemExit
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    with pytest.raises(SystemExit):
        mod.whisper_reaper()
    assert mod._whisper is None


def test_stale_session_starts_fresh(agent, monkeypatch):
    mod, _ = agent
    mod.kv_set(mod.K("session_id", mod.PRIMARY), "old-pc-session")
    calls = []

    def fake_run(prompt, model, sid, *a, **k):
        calls.append(sid)
        if sid:
            raise RuntimeError("claude returned no result: No conversation found with session ID: old-pc-session")
        return {"result": "שלום", "session_id": "new"}
    monkeypatch.setattr(mod, "_run_claude", fake_run)
    monkeypatch.setattr(mod, "session_rotates", lambda *a: False)
    assert mod.claude("hi", on_stage=lambda *a: None) == "שלום"
    assert calls == ["old-pc-session", None] and mod.kv_get(mod.K("session_id", mod.PRIMARY)) == "new"


def test_skip_groups_from_transcription(agent, monkeypatch):
    mod, db = agent
    db.execute("INSERT OR REPLACE INTO chats VALUES ('111@g.us', 'משרות QA - HireMe', '2026-11-10 09:00:00+02:00')")
    db.execute("INSERT INTO messages VALUES ('g1', '111@g.us', 'x', '', datetime('now','localtime'), 0, 'audio', '')")
    db.commit()
    assert "משרות QA" in mod.owner_command("/תמלול")  # shown among the busiest groups
    assert "✅" in mod.owner_command("/תמלול דלג משרות")
    calls = []
    monkeypatch.setattr(mod, "transcript_of", lambda *a: calls.append(a) or "x")
    mod.transcribe_and_post("111@g.us", "g2", "x", "2026-11-10 10:00:00+02:00")
    assert calls == []  # skipped: not transcribed, not posted
    assert "משרות QA" in mod.owner_command("/תמלול רשימה").split("🔇")[1]
    assert "חוזרים" in mod.owner_command("/תמלול החזר משרות")
    assert not mod.is_skipped("111@g.us")
