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
