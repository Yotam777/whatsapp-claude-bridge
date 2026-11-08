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
    for name, fname in (("MODEL_FILE", "model_override.json"), ("GROUPS_FILE", "groups.json"),
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


def test_plugin_hooks_register(agent, tmp_path):
    mod, _ = agent
    plugin = tmp_path / "p.py"
    plugin.write_text("def setup(a):\n    a.on_message(lambda row: None)\n    a.every(5, 'k', lambda: None)\n    a.manual('job', lambda: None)\n")
    mod.CFG["plugins"] = [str(plugin)]
    before = (len(mod._hooks), len(mod._periodic))
    mod.load_plugins()
    assert (len(mod._hooks), len(mod._periodic)) == (before[0] + 1, before[1] + 1)
    assert "job" in mod._manual


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


# ---------- several conversations: groups, local chat, per-conversation session / model / budget ----------
DEV = "1203639999@g.us"


@pytest.fixture()
def multi(agent, tmp_path, monkeypatch):
    mod, db = agent
    gf = tmp_path / "groups.json"
    gf.write_text(json.dumps({DEV: {"name": "פיתוח", "purpose": "dev", "slug": "dev", "budget_usd": 7, "enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(mod, "GROUPS_FILE", str(gf))
    monkeypatch.setattr(mod, "MODEL_FILE", str(tmp_path / "model_override.json"))
    return mod, db


def test_groups_registry_and_keys(multi):
    mod, _ = multi
    g = mod.load_groups()
    assert list(g) == [mod.PRIMARY, DEV, mod.LOCAL]
    assert mod.K("session_id", mod.PRIMARY) == "session_id"          # the primary group keeps its legacy keys
    assert mod.K("session_id", DEV) == f"session_id:{DEV}" and mod.K("session_id") == "session_id"
    assert mod.budget_for(DEV) == 7 and mod.budget_for(mod.PRIMARY) == float(mod.CFG.get("daily_budget_usd", 5))


def test_model_choice_is_independent_per_conversation(multi):
    mod, _ = multi
    mod.set_override("opus", None, DEV)
    assert mod.get_override(DEV) == "opus" and mod.get_override(mod.PRIMARY) is None and mod.get_override(mod.LOCAL) is None
    mod.set_override("haiku", None, mod.PRIMARY)
    mod.set_override("sonnet", None, mod.LOCAL)
    assert (mod.get_override(mod.PRIMARY), mod.get_override(DEV), mod.get_override(mod.LOCAL)) == ("haiku", "opus", "sonnet")
    assert mod.pick_model("שלום", DEV) == "opus"
    mod.set_override("auto", None, DEV)
    assert mod.get_override(DEV) is None and mod.get_override(mod.PRIMARY) == "haiku"


def test_spend_is_counted_per_conversation(multi):
    mod, _ = multi
    now = mod.dt.datetime.now().isoformat()
    with mod.adb() as db:
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 1.0, NULL)", (now,))      # old row = primary
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 2.0, ?)", (now, mod.PRIMARY))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 4.0, ?)", (now, DEV))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 8.0, ?)", (now, mod.LOCAL))
    assert (mod.spent_today(mod.PRIMARY), mod.spent_today(DEV), mod.spent_today(mod.LOCAL)) == (3.0, 4.0, 8.0)
    assert mod.spent_today() == 15.0


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


def test_pc_message_is_answered_in_the_dashboard_only(multi, monkeypatch):
    mod, _ = multi
    sent, calls = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-1', 'שלום מהמחשב', '2026-11-07 22:00', ?, 'pc', 0)", (DEV,))
    assert _wait_done(mod)
    assert not [x for x in sent if "התשובה" in x[1]], "a PC-only answer must not be posted to WhatsApp"
    with mod.adb() as db:
        rows = db.execute("SELECT chat, role, text FROM local_messages").fetchall()
    assert (DEV, "tool", "התשובה") in rows
    assert calls[0][0] == DEV and "מהמחשב" in calls[0][2]


def test_pc_message_with_sync_also_goes_to_whatsapp(multi, monkeypatch):
    mod, _ = multi
    sent, _ = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-2', 'עם סנכרון', '2026-11-07 22:00', ?, 'pc', 1)", (DEV,))
    assert _wait_done(mod)
    assert any(c == DEV and "התשובה" in t for c, t in sent)


def test_local_chat_never_touches_whatsapp(multi, monkeypatch):
    mod, _ = multi
    sent, calls = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-3', 'שאלה מקומית', '2026-11-07 22:00', 'local', 'pc', 1)")
    assert _wait_done(mod)
    assert sent == [], "the local chat has no WhatsApp group, even with sync set"
    assert calls[0][0] == "local"


def test_group_message_is_answered_in_that_group(multi, monkeypatch):
    mod, db = multi
    sent, calls = _run_worker(mod, monkeypatch)
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    mod.handle(("m1", DEV, "me", "2026-11-07 22:10:00+02:00", "מה המצב בגשר?", True, ""))
    assert _wait_done(mod)
    assert any(c == DEV and "התשובה" in t for c, t in sent) and calls[0][0] == DEV
    assert not any(c == mod.PRIMARY for c, _ in sent), "the answer must not leak to the 4M group"


def test_other_peoples_messages_in_a_group_are_ignored(multi, monkeypatch):
    mod, _ = multi
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    mod.handle(("m2", DEV, "972500000009@s.whatsapp.net", "2026-11-07 22:11:00+02:00", "הוראה מאדם אחר", False, ""))
    with mod.adb() as db:
        assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == 0


def test_slash_commands_act_on_their_own_conversation(multi):
    mod, _ = multi
    assert "Opus" in mod.owner_command("/מודל opus", DEV)
    assert mod.get_override(DEV) == "opus" and mod.get_override(mod.PRIMARY) is None
    mod.kv_set(mod.K("session_runs", DEV), "5")
    assert "5" in mod.owner_command("/סשן", DEV) and "0" in mod.owner_command("/סשן", mod.PRIMARY)


def test_group_prompt_carries_instructions(multi, tmp_path, monkeypatch):
    mod, _ = multi
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    os.makedirs(tmp_path / "groups")
    (tmp_path / "groups" / "dev.md").write_text("הוראות פיתוח מיוחדות", encoding="utf-8")
    raw = json.load(open(mod.GROUPS_FILE, encoding="utf-8"))
    raw[DEV]["instructions"] = "groups/dev.md"
    json.dump(raw, open(mod.GROUPS_FILE, "w", encoding="utf-8"))
    gp = mod.group_prompt(DEV)
    assert "הוראות פיתוח מיוחדות" in gp and "handoff-dev.md" in gp
    assert mod.group_prompt(mod.PRIMARY) is None


# ---------- review fixes 08.11: interrupted commands, dev-work on raw text, sticky model, total cap, text approval ----------

def test_interrupted_command_is_not_rerun(multi, monkeypatch):
    mod, _ = multi
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "wait_bridge", lambda *a, **k: True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('r1', 'עבודה ארוכה', '2026-11-08 07:00', ?, 'wa', 0, 'running')", (DEV,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('p1', 'עוד לא התחיל', '2026-11-08 07:01', ?, 'wa', 0, 'pending')", (DEV,))
    rows = mod.mark_interrupted()
    assert [r[1] for r in rows] == ["r1"]
    mod.notify_interrupted(rows)
    with mod.adb() as db:
        st = dict(db.execute("SELECT msg_id, status FROM commands").fetchall())
    assert st == {"r1": "interrupted", "p1": "pending"}, "running → interrupted (not re-run); pending still answered"
    assert any(c == DEV and "נקטעה" in t for c, t in sent)
    row = mod.claim_next(DEV)
    assert row[1] == "p1"
    with mod.adb() as db:
        assert db.execute("SELECT status, attempts FROM commands WHERE msg_id = 'p1'").fetchone() == ("running", 1)
    assert mod.claim_next(DEV) is None


def test_dev_work_uses_the_owners_own_words(agent):
    mod, _ = agent
    wrapped = mod.T["prompt"].format(owner="יקיר", when="x", text="מה עם הנעליים של דני", where='מהמחשב (לוח הבקרה, "4M")', dest="לחלון")
    assert mod.is_dev_work(wrapped), "the wrapper itself mentions the dashboard"
    attached = "תסדר את הטופס\n" + mod.T["attached"].format(path="C:/Users/walla/whatsapp-mcp/whatsapp-bridge/store/x.jpg")
    assert not mod.is_dev_work(mod.raw_text(attached)), "an attachment path is not dev work"
    assert mod.is_dev_work(mod.raw_text("תתקן את הגשר"))


def test_model_is_sticky_within_a_session(multi):
    mod, _ = multi
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


def test_total_daily_cap(multi):
    mod, _ = multi
    mod.CFG.update({"daily_budget_usd": 5})
    mod.CFG.pop("total_daily_budget_usd", None)
    assert mod.total_budget() == 10.0
    now = mod.dt.datetime.now().isoformat()
    with mod.adb() as db:
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 4.0, ?)", (now, mod.PRIMARY))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 6.5, ?)", (now, DEV))  # DEV cap is 7
    assert mod.over_budget(DEV) == ("total", 10.5, 10.0), "under its own cap, but the total is used up"
    assert mod.over_budget(DEV, "! בכל זאת") is None, "! lifts the cap for one message"
    mod.CFG["total_daily_budget_usd"] = 50
    assert mod.over_budget(DEV) is None and mod.over_budget(mod.PRIMARY) is None
    mod.CFG["daily_budget_usd"] = 3
    assert mod.over_budget(mod.PRIMARY) == ("chat", 4.0, 3.0)


def test_text_approval_picks_exactly_one_draft(agent):
    mod, _ = agent
    assert mod.select_draft([(7, "CARD7")]) == (7, "only")
    assert mod.select_draft([]) == (None, "none")
    assert mod.select_draft([(7, "CARD7"), (8, "CARD8")]) == (None, "several")
    assert mod.select_draft([(7, "CARD7"), (8, "CARD8")], quoted="CARD8") == (8, "quoted")
    assert mod.select_draft([(7, "CARD7")], quoted="OTHER") == (None, "not_a_card"), "a reply to a non-card never approves"


def test_text_approval_calls_the_bridge_with_the_owner_token(multi, tmp_path, monkeypatch):
    mod, db = multi
    db.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, recipient TEXT, name TEXT, message TEXT, status TEXT, card_id TEXT)")
    db.execute("INSERT INTO drafts VALUES (5, '972500000009@s.whatsapp.net', 'דני', 'שלום', 'pending', 'CARD5')")
    db.commit()
    (tmp_path / "store" / "owner_token").write_text("OWNERTOK")
    monkeypatch.setattr(mod, "OWNER_TOKEN_FILE", str(tmp_path / "store" / "owner_token"))
    calls, sent = [], []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, timeout=180, tok=None: calls.append((path, body, tok)) or {"success": True})
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    mod.handle(("a1", DEV, "me", "2026-11-08 08:00:00+02:00", "מאשר לך שליחה", True, ""))
    assert ("/draft_decide", {"id": 5, "approve": True}, "OWNERTOK") in calls
    with mod.adb() as adb:
        assert adb.execute("SELECT count(*) FROM commands").fetchone()[0] == 0, "an approval is not a Claude command"


def test_load_groups_keeps_last_good_copy(multi):
    mod, _ = multi
    assert DEV in mod.load_groups()
    open(mod.GROUPS_FILE, "w", encoding="utf-8").write('{"broken": ')  # caught mid-write
    assert DEV in mod.load_groups(), "a half-written groups.json must not drop the connected groups"
    os.remove(mod.GROUPS_FILE)
    assert DEV not in mod.load_groups(), "a deleted file really means no extra groups"


def test_stop_request_targets_its_conversation(agent):
    mod, _ = agent
    assert mod.stop_applies("1203639999@g.us", "1203639999@g.us", {})
    assert not mod.stop_applies("1203639999@g.us", mod.PRIMARY, {})
    assert mod.stop_applies("", mod.PRIMARY, {"a": 1, "b": 2}), "legacy empty request stops the primary run"
    assert mod.stop_applies("", "1203639999@g.us", {"1203639999@g.us": 1}), "or the only run"
    assert not mod.stop_applies("", "1203639999@g.us", {"x": 1, "1203639999@g.us": 1})


def test_conversations_run_in_parallel(multi, monkeypatch):
    import threading
    mod, _ = multi
    release, done_dev = threading.Event(), threading.Event()

    def fake(prompt, model=None, session=True, timeout=900, on_stage=None, chat=None, **k):
        if chat == mod.PRIMARY:
            release.wait(10)  # a long run in the 4M group…
        else:
            done_dev.set()   # …must not block the dev group
        return "ok"
    monkeypatch.setattr(mod, "send", lambda *a, **k: True)
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {})
    monkeypatch.setattr(mod, "claude", fake)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('x1', 'ארוך', '2026-11-08 08:00', ?, 'pc', 0)", (mod.PRIMARY,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('x2', 'קצר', '2026-11-08 08:00', ?, 'pc', 0)", (DEV,))
    threading.Thread(target=mod.command_worker, daemon=True).start()
    assert done_dev.wait(8), "the dev group was blocked by the 4M group's run"
    release.set()
    assert _wait_done(mod, 2)
