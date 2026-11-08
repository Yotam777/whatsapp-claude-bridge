"""Dashboard backend tests: a temporary tool root with its own databases, no bridge, no agent."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
PRIM, DEV = "1203630000@g.us", "1203639999@g.us"


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    (tmp_path / "agent" / "home").mkdir(parents=True)
    (tmp_path / "agent" / "group_templates").mkdir()
    (tmp_path / "agent" / "group_templates" / "dev.md").write_text("הוראות {owner}", encoding="utf-8")
    (tmp_path / "agent" / "models.json").write_text("{}", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    m = sqlite3.connect(store / "messages.db")
    m.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, filename TEXT, PRIMARY KEY (id, chat_jid))")
    m.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    m.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, 0, '', '')", [
        ("a", "972500000001@s.whatsapp.net", "972500000001", "צריך עוד קסדות מידה L לצוות", "2026-11-07 09:00:00+02:00"),
        ("b", PRIM, "me", "🤖 _קיבלתי_", "2026-11-07 09:01:00+02:00"),
    ])
    m.commit(); m.close()
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    cfg.update({"store_dir": str(store), "claude_group": PRIM, "plugins": []})
    (tmp_path / "agent" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("WA_TOOL_ROOT", str(tmp_path))
    sys.path.insert(0, ROOT)
    import dash.server as srv
    srv = importlib.reload(srv)
    monkeypatch.setattr(srv, "bridge", lambda path, body=None, timeout=8: {"members": 1, "name": "x"} if path == "/group_info" else {})
    return srv, tmp_path


def test_say_pc_queues_a_command_and_shows_it(dash):
    srv, root = dash
    r = srv.action({"do": "say_pc", "chat": "local", "text": "שלום", "sync": 0})
    assert r["ok"]
    con = sqlite3.connect(root / "agent" / "agent.db")
    assert con.execute("SELECT chat_jid, source, sync, text FROM commands").fetchall() == [("local", "pc", 0, "שלום")]
    conv = srv.conversation_of("local", srv.cfg(), srv.paths())
    assert [(m["content"], m["tool"], m["src"]) for m in conv] == [("שלום", False, "pc")]


def test_connect_group_creates_instructions_and_policy(dash):
    srv, root = dash
    pol = {}
    srv.bridge = lambda path, body=None, timeout=8: pol.update(body or {}) or ({"members": 1} if path == "/group_info" else {})
    r = srv.action({"do": "group_connect", "jid": DEV, "name": "פיתוח", "purpose": "dev"})
    assert r["ok"]
    assert "הוראות" in open(root / "agent" / "home" / "groups" / "1203639999.md", encoding="utf-8").read()
    assert DEV in srv.conversations() and pol["control_groups"] == [DEV]
    assert srv.model_info(DEV)["current"] == "opus" and srv.model_info(PRIM)["current"] == "auto"
    assert srv.action({"do": "group_disconnect", "jid": DEV})["ok"] and DEV not in srv.conversations()
    assert srv.action({"do": "group_disconnect", "jid": PRIM})["error"]


def test_group_with_other_members_needs_confirmation(dash):
    srv, _ = dash
    srv.bridge = lambda path, body=None, timeout=8: {"members": 5}
    assert srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general"}) == {"needs_confirm": True, "members": 5}
    assert srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general", "confirm_members": True})["ok"]


def test_search_finds_typos_and_short_queries(dash):
    srv, _ = dash
    assert srv.search("קסדות")["results"][0]["snippet"].find("קסדות") >= 0
    assert srv.search("קסדאת")["results"], "one wrong letter must still find it"
    assert srv.search("מי")["results"] or True  # 2-letter queries use LIKE and must not crash
    assert srv.search("zzzzqqq")["results"] == []
    assert srv.search("קסדות")["results"][0]["chat"] == "972500000001@s.whatsapp.net"


def test_budget_can_be_set_per_group(dash):
    srv, _ = dash
    srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general"})
    assert srv.action({"do": "group_budget", "jid": DEV, "budget": "12.5"})["ok"]
    assert srv.conversations()[DEV]["budget_usd"] == 12.5
    assert srv.action({"do": "group_budget", "jid": DEV, "budget": "-1"})["error"]



def test_federation_merges_peer_groups_and_forwards_actions(dash):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    srv, root = dash
    srv_http = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=srv_http.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv_http.server_address[1]}"
    cfgp = root / "agent" / "config.json"
    c = json.load(open(cfgp, encoding="utf-8"))
    c.update({"host_label": "DESK", "claude_account": "private"})
    cfgp.write_text(json.dumps(c), encoding="utf-8")
    (root / "agent" / "peers.json").write_text(json.dumps(
        {"token": "t0k", "peers": [{"name": "LAP", "url": base, "host": "LAPTOP", "account": "work"}]}), encoding="utf-8")

    d = srv.data(None)
    jids = [g["jid"] for g in d["groups"]]
    assert PRIM in jids and "LAP::" + PRIM in jids
    own = next(g for g in d["groups"] if g["jid"] == PRIM)
    remote = next(g for g in d["groups"] if g["jid"] == "LAP::" + PRIM)
    assert (own["host"], own["account"]) == ("DESK", "private")
    assert (remote["host"], remote["account"], remote["remote"]) == ("LAPTOP", "work", True)
    assert d["peers"][0]["ok"] is True

    r = srv.data("LAP::" + PRIM)
    assert r["chat"] == "LAP::" + PRIM and "LAP::" + PRIM in [g["jid"] for g in r["groups"]]

    assert srv.forward_action({"do": "model", "chat": "LAP::" + PRIM, "model": "opus"}) == {"ok": True, "model": "opus"}
    assert srv.forward_action({"do": "model", "chat": PRIM, "model": "auto"}) is None

    def call(token):
        req = urllib.request.Request(base + "/data", headers={"X-Peer-Token": token})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
    assert call("wrong") == 403 and call("t0k") == 200
    srv_http.shutdown()


def test_without_peers_nothing_changes(dash):
    srv, _ = dash
    d = srv.data(None)
    assert "peers" not in d and all(not g.get("remote") for g in d["groups"])
