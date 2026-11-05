"""Dashboard backend of the WhatsApp ↔ Claude tool.

Standalone: reads the tool's local databases directly (read-only) and talks to the bridge when it is up, so the
dashboard works without the agent, without Claude and without internet. Served by the desktop app (WhatsAppDash.exe)
and by the agent on http://127.0.0.1:8770. Bound to localhost only.
"""
import datetime as dt
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PKG = getattr(sys, "_MEIPASS", None) and os.path.join(sys._MEIPASS, "dash") or os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(PKG, "static")
VERSION = open(os.path.join(PKG, "VERSION")).read().strip()
SETTINGS = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WhatsAppDash", "settings.json")


def _root():
    """Where the tool is installed: env WA_TOOL_ROOT, the app's settings.json, or the source checkout."""
    if os.environ.get("WA_TOOL_ROOT"):
        return os.environ["WA_TOOL_ROOT"]
    try:
        return json.load(open(SETTINGS, encoding="utf-8"))["tool_root"]
    except Exception:
        return os.path.dirname(PKG)


ROOT = _root()


def cfg():
    return json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))


def paths():
    c = cfg()
    store = os.path.expanduser(c["store_dir"])
    store = store if os.path.isabs(store) else os.path.join(ROOT, store)
    return {"msg": os.path.join(store, "messages.db"), "wa": os.path.join(store, "whatsapp.db"),
            "agent": os.path.join(ROOT, "agent", "agent.db"), "log": os.path.join(ROOT, "agent", "agent.log"),
            "token": os.path.join(store, "api_token"), "policy": os.path.join(store, "send_policy.json"),
            "bridge_log": os.path.join(ROOT, "whatsapp-bridge", "bridge.log")}


def bridge(path, body=None, timeout=8):
    c, p = cfg(), paths()
    try:
        tok = open(p["token"]).read().strip()
    except OSError:
        tok = ""
    req = urllib.request.Request(c["bridge_url"] + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def rows(db, sql, args=()):
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


def kv(k):
    r = rows(paths()["agent"], "SELECT v FROM kv WHERE k = ?", (k,))
    return r[0]["v"] if r else None


def names():
    p = paths()
    nm = {}
    for r in rows(p["wa"], "SELECT their_jid, full_name, push_name FROM whatsmeow_contacts"):
        nm[r["their_jid"].split("@")[0]] = r["full_name"] or r["push_name"]
    for r in rows(p["wa"], "SELECT lid, pn FROM whatsmeow_lid_map"):
        if r["pn"] in nm:
            nm[r["lid"]] = nm[r["pn"]]
    for r in rows(p["msg"], "SELECT jid, name FROM chats WHERE jid LIKE '%@g.us'"):
        nm[r["jid"].split("@")[0]] = r["name"]
    return nm


def local_policy():
    try:
        return json.load(open(paths()["policy"], encoding="utf-8"))
    except Exception:
        return {"mode": "draft"}


def data():
    c, p = cfg(), paths()
    nm = names()
    label = lambda j: nm.get(str(j).split("@")[0]) or str(j).split("@")[0]
    group = c["claude_group"]
    day0 = (dt.datetime.now() - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    today = dt.date.today().isoformat()
    b = bridge("/status")
    jl = lambda k: json.loads(kv(k)) if kv(k) else None
    convo = rows(p["msg"], "SELECT id, timestamp, is_from_me, content, media_type FROM messages WHERE chat_jid = ? "
                           "ORDER BY rowid DESC LIMIT 10", (group,))[::-1]
    for m in convo:
        m["tool"] = (m["content"] or "").startswith(c["reply_prefix"].strip())
        if m["tool"]:
            m["content"] = m["content"][len(c["reply_prefix"].strip()):].strip()
    hourly = rows(p["msg"], "SELECT substr(timestamp,12,2) h, count(*) n FROM messages WHERE substr(timestamp,1,19) > ? GROUP BY 1", (day0,))
    spent = rows(p["agent"], "SELECT coalesce(sum(cost),0) s FROM claude_runs WHERE substr(at,1,10) = ?", (today,))
    return {
        "version": VERSION,
        "now": dt.datetime.now().isoformat(timespec="seconds"),
        "bridge": b,
        "policy": b.get("policy") if "policy" in b else local_policy(),
        "conn_log": rows(p["msg"], "SELECT at, event FROM conn_log WHERE at > ? ORDER BY at", (day0,)),
        "hourly": hourly,
        "received_today": (rows(p["msg"], "SELECT count(*) n FROM messages WHERE is_from_me = 0 AND substr(timestamp,1,10) = ?", (today,)) or [{"n": 0}])[0]["n"],
        "agent": {"heartbeat": kv("heartbeat"), "session_started": kv("session_started"), "backup_done": kv("backup_done"),
                  "events_connected": kv("events_connected"), "plugins": [os.path.basename(x) for x in c.get("plugins", [])],
                  "pending": (rows(p["agent"], "SELECT count(*) n FROM commands WHERE status = 'pending'") or [{"n": 0}])[0]["n"],
                  "budget": c.get("daily_budget_usd", 5), "spent_today": round(spent[0]["s"], 2) if spent else 0},
        "conversation": convo,
        "last_owner": jl("last_owner_msg"), "last_tool": jl("last_tool_msg"),
        "drafts": rows(p["msg"], "SELECT id, name, recipient, message, created, status FROM drafts ORDER BY id DESC LIMIT 40"),
        "sent": [dict(r, recipient=label(r["recipient"])) for r in
                 rows(p["msg"], "SELECT recipient, ok, detail, timestamp FROM send_log ORDER BY id DESC LIMIT 40")],
        "transcripts": [dict(r, sender=label(r["sender"]), chat=label(r["chat_jid"])) for r in
                        rows(p["agent"], "SELECT chat_jid, sender, ts, text FROM transcripts ORDER BY ts DESC LIMIT 40")],
        "transcribed_today": (rows(p["agent"], "SELECT count(*) n FROM transcripts WHERE substr(ts,1,10) = ?", (today,)) or [{"n": 0}])[0]["n"],
        "costs": rows(p["agent"], "SELECT substr(at,1,10) day, round(sum(cost),2) cost, count(*) runs FROM claude_runs GROUP BY 1 ORDER BY 1 DESC LIMIT 14")[::-1],
        "log": open(p["log"], encoding="utf-8").read().splitlines()[-60:] if os.path.exists(p["log"]) else [],
    }


def kill(name_like):
    """Stop a tool process; its run-*.cmd loop restarts it within seconds."""
    ps = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{name_like}*' -and $_.Name -ne 'cmd.exe' "
          f"-and $_.Name -ne 'conhost.exe' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def action(a):
    kind = a.get("do")
    p = paths()
    if kind in ("mode", "pause", "resume"):
        body = {}
        if kind == "mode":
            body = {"mode": a["mode"]}
            if a["mode"] == "auto":
                body["auto_until"] = (dt.datetime.now().astimezone() + dt.timedelta(hours=float(a.get("hours", 1)))).isoformat(timespec="seconds")
        elif kind == "pause":
            body = {"paused_until": (dt.datetime.now().astimezone() + dt.timedelta(hours=float(a.get("hours", 6)))).isoformat(timespec="seconds"),
                    "pause_reason": "הושהה ידנית מלוח הבקרה"}
        else:
            body = {"paused_until": "", "pause_reason": ""}
        res = bridge("/policy", body)
        if "error" in res:  # bridge down: write the policy file directly; the bridge reads it on every send
            pol = local_policy()
            pol.update(body)
            if pol.get("mode") != "auto":
                pol["mode"], pol["auto_until"] = "draft", ""
            json.dump(pol, open(p["policy"], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            res = pol
        return res
    if kind == "draft":
        con = sqlite3.connect(p["msg"], timeout=10)
        con.execute("UPDATE drafts SET status = ? WHERE id = ?", (a["status"], int(a["id"])))
        con.commit()
        con.close()
        return {"ok": True}
    if kind == "backup":
        import shutil
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        subprocess.Popen([uv, "run", "-q", "python", "-X", "utf8", "agent.py", "backup"],
                         cwd=os.path.join(ROOT, "agent"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": True}
    if kind == "restart":
        kill({"bridge": "whatsapp-bridge.exe", "agent": "agent.py"}[a["what"]])
        return {"ok": True}
    if kind == "update_check":
        from dash import updater
        return updater.check()
    if kind == "update_apply":
        from dash import updater
        return updater.apply()
    return {"error": "unknown action"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_bytes(self, b, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8", code)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/data":
            try:
                return self.send_json(data())
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        rel = "index.html" if path in ("/", "") else path.lstrip("/").replace("static/", "", 1)
        f = os.path.normpath(os.path.join(STATIC, rel))
        if not f.startswith(os.path.normpath(STATIC)) or not os.path.isfile(f):
            return self.send_bytes(b"not found", "text/plain", 404)
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(open(f, "rb").read(), ctype)

    def do_POST(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{host}",):  # same-origin only
            return self.send_json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length", 0))
        try:
            return self.send_json(action(json.loads(self.rfile.read(n) or b"{}")))
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)


def serve(port=8770):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()


def start_free_port():
    """For the desktop app: start on any free localhost port, return it."""
    import threading
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]
