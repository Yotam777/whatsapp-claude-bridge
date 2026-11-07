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
        return json.load(open(SETTINGS, encoding="utf-8-sig"))["tool_root"]  # PowerShell 5 writes a BOM
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
                           "ORDER BY rowid DESC LIMIT 60", (group,))[::-1]
    for m in convo:
        m["tool"] = (m["content"] or "").startswith(c["reply_prefix"].strip())
        if m["tool"]:
            m["content"] = m["content"][len(c["reply_prefix"].strip()):].strip()
    hourly = rows(p["msg"], "SELECT substr(timestamp,12,2) h, count(*) n FROM messages WHERE substr(timestamp,1,19) > ? GROUP BY 1", (day0,))
    spent = rows(p["agent"], "SELECT coalesce(sum(cost),0) s FROM claude_runs WHERE substr(at,1,10) = ?", (today,))
    return {
        "version": VERSION,
        "tool_version": (open(os.path.join(ROOT, "VERSION")).read().strip() if os.path.exists(os.path.join(ROOT, "VERSION")) else "?"),
        "maintainer": bool(c.get("maintainer")),
        "now": dt.datetime.now().isoformat(timespec="seconds"),
        "bridge": b,
        "policy": b.get("policy") if "policy" in b else local_policy(),
        "conn_log": rows(p["msg"], "SELECT at, event FROM (SELECT at, event FROM conn_log WHERE at <= ? ORDER BY at DESC LIMIT 1) "
                                   "UNION ALL SELECT at, event FROM conn_log WHERE at > ? ORDER BY at", (day0, day0)),
        "received": [dict(r, sender=label(r["sender"]), chat=nm.get(str(r["chat_jid"]).split("@")[0]) or r["chat_name"] or label(r["chat_jid"]), group=str(r["chat_jid"]).endswith("@g.us")) for r in
                     rows(p["msg"], "SELECT m.chat_jid, m.sender, m.timestamp, m.content, m.media_type, c.name AS chat_name FROM messages m "
                                    "LEFT JOIN chats c ON c.jid = m.chat_jid WHERE m.is_from_me = 0 "
                                    "AND substr(m.timestamp,1,10) = ? ORDER BY m.timestamp DESC LIMIT 300", (today,))],
        "runs_today": rows(p["agent"], "SELECT * FROM claude_runs WHERE substr(at,1,10) = ? ORDER BY at DESC LIMIT 60", (today,)),
        "hourly": hourly,
        "received_today": (rows(p["msg"], "SELECT count(*) n FROM messages WHERE is_from_me = 0 AND substr(timestamp,1,10) = ?", (today,)) or [{"n": 0}])[0]["n"],
        "agent": {"heartbeat": kv("heartbeat"), "session_started": kv("session_started"), "backup_done": kv("backup_done"),
                  "events_connected": kv("events_connected"), "plugins": [os.path.basename(x) for x in c.get("plugins", [])],
                  "pending": (rows(p["agent"], "SELECT count(*) n FROM commands WHERE status = 'pending'") or [{"n": 0}])[0]["n"],
                  "budget": c.get("daily_budget_usd", 5), "spent_today": round(spent[0]["s"], 2) if spent else 0},
        "model": model_info(),
        "conversation": convo,
        "last_owner": jl("last_owner_msg"), "last_tool": jl("last_tool_msg"),
        "drafts": rows(p["msg"], "SELECT id, name, recipient, message, created, status FROM drafts ORDER BY id DESC LIMIT 40"),
        "sent": [dict(r, recipient=label(r["recipient"])) for r in
                 rows(p["msg"], "SELECT s.recipient, s.ok, s.detail, s.timestamp, s.recipient = ? AS own, "
                      "(SELECT substr(m.content, 1, 400) FROM messages m WHERE m.id = s.message_id LIMIT 1) AS text "
                      "FROM send_log s ORDER BY s.id DESC LIMIT 60", (c.get("claude_group", ""),))],
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


def git_action(kind, a):
    """Maintainer only (config.json "maintainer": true): save to git and publish a release from the dashboard."""
    if not cfg().get("maintainer"):
        return {"error": "not enabled (set maintainer: true in agent/config.json)"}
    import re
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    git = lambda *x: subprocess.run(["git", *x], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120, creationflags=nw)
    if kind == "git_status":
        ver = open(os.path.join(ROOT, "VERSION")).read().strip()
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver)
        nxt = f"{m[1]}.{m[2]}.{int(m[3]) + 1}" if m else ver
        changed = [l for l in git("status", "--porcelain").stdout.splitlines() if l.strip()]
        return {"version": ver, "next": nxt, "branch": git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                "changed": len(changed), "files": [l[3:] for l in changed[:15]], "last": git("log", "-1", "--format=%s").stdout.strip()}
    notes = (a.get("notes") or "").strip()
    if kind == "git_commit":
        if not notes:
            return {"error": "write a short description of the change"}
        git("add", "-A")  # personal data is excluded by .gitignore; the release scan checks again
        r = git("commit", "-m", notes)
        return {"ok": r.returncode == 0, "out": (r.stdout + r.stderr)[-400:]}
    ver = a.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        return {"error": "version must look like 1.0.2"}
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        return {"error": "there are unsaved changes - press 'save to git' first"}
    import tempfile
    nf = os.path.join(tempfile.gettempdir(), "wa_release_notes.txt")
    open(nf, "w", encoding="utf-8").write(notes)
    # visible window: the user watches the checks, the push and any error
    subprocess.Popen(["cmd.exe", "/c", "start", "Release", "cmd", "/k", os.path.join(ROOT, "release.cmd"), ver, f"--notes-file={nf}"], cwd=ROOT)
    return {"ok": True}


def _model_file():
    return os.path.join(ROOT, "agent", "model_override.json")


def model_info():
    """Model selection shared with the agent (agent/model_override.json) + the guide shown as tooltips."""
    cur, until = "auto", ""
    try:
        o = json.load(open(_model_file(), encoding="utf-8"))
        cur, until = o.get("model") or "auto", o.get("until") or ""
        if until and until < dt.datetime.now().isoformat(timespec="seconds"):
            cur, until = "auto", ""
    except Exception:
        pass
    try:
        guide = json.load(open(os.path.join(ROOT, "agent", "models.json"), encoding="utf-8"))
    except Exception:
        guide = {}
    return {"current": cur, "until": until, "last": kv("last_model"), "recommend": (json.loads(kv("last_recommend")) if kv("last_recommend") else None),
            "session_runs": kv("session_runs"), "guide": guide}


def action(a):
    kind = a.get("do")
    p = paths()
    if kind == "model":
        m = a.get("model", "auto")
        until = (dt.datetime.now() + dt.timedelta(hours=float(a["hours"]))).isoformat(timespec="seconds") if a.get("hours") else ""
        json.dump({"model": m, "until": until}, open(_model_file(), "w", encoding="utf-8"))
        return {"ok": True, "model": m}
    if kind == "say":  # continue the conversation from the dashboard: a normal message from the owner in the control group
        text = (a.get("text") or "").strip()
        if not text:
            return {"error": "empty"}
        res = bridge("/send", {"recipient": cfg()["claude_group"], "message": text}, timeout=60)  # the bridge paces sends: 8 s was too short, the app showed an error and the owner re-sent
        return res if "error" in res else {"ok": True}
    if kind == "group_info":  # the control group's live name (dashboard card)
        g = cfg().get("claude_group", "")
        res = bridge("/group_info", {"jid": g}) if g else {"error": "no control group"}
        res["jid"] = g
        return res
    if kind == "group_rename":  # rename the control group (owner asked from the dashboard)
        name = (a.get("name") or "").strip()
        if not name or len(name) > 25:
            return {"error": "שם בין 1 ל-25 תווים"}
        return bridge("/set_group", {"jid": cfg()["claude_group"], "name": name})
    if kind == "group_create":  # create a new dedicated control group (only this account) and point the agent at it
        name = (a.get("name") or "").strip()
        if not name or len(name) > 25:
            return {"error": "שם בין 1 ל-25 תווים"}
        res = bridge("/create_group", {"name": name, "participants": [], "description": "השיחה הפרטית שלך עם Claude"}, timeout=40)
        if not str(res.get("jid", "")).endswith("@g.us"):
            return res if "error" in res else {"error": "create failed"}
        cpath = os.path.join(ROOT, "agent", "config.json")
        c = json.load(open(cpath, encoding="utf-8"))
        old = c.get("claude_group", "")
        c["claude_group"] = res["jid"]
        json.dump(c, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        pol = bridge("/policy", {"control_group": res["jid"]})
        if "error" in pol:  # bridge reads the policy file on every send
            lp = local_policy(); lp["control_group"] = res["jid"]
            json.dump(lp, open(paths()["policy"], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        kill("agent.py")  # the agent reads claude_group only at start; run-agent.cmd restarts it
        return {"ok": True, "jid": res["jid"], "old": old}
    if kind == "draft_decide":
        res = bridge("/draft_decide", {"id": int(a["id"]), "approve": bool(a.get("approve"))})
        if "error" in res and not a.get("approve"):  # bridge down: cancelling needs no bridge
            return action({"do": "draft", "id": a["id"], "status": "cancelled"})
        return res
    if kind in ("pause", "resume"):
        body = {}
        if kind == "pause":
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
        if a["status"] not in ("cancelled", "discarded", "sent"):
            return {"error": "bad status"}
        con = sqlite3.connect(p["msg"], timeout=10)
        con.execute("UPDATE drafts SET status = ? WHERE id = ?", (a["status"], int(a["id"])))
        con.commit()
        con.close()
        return {"ok": True}
    if kind == "backup":
        import shutil
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        subprocess.Popen([uv, "run", "-q", "--no-sync", "python", "-X", "utf8", "agent.py", "backup"],
                         cwd=os.path.join(ROOT, "agent"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": True}
    if kind == "restart":
        if a.get("what") == "all":  # bridge first; the agent (which may host this server) a moment later, after we answer
            import threading
            kill("whatsapp-bridge.exe")
            threading.Timer(1.5, kill, args=("agent.py",)).start()
            return {"ok": True}
        kill({"bridge": "whatsapp-bridge.exe", "agent": "agent.py"}[a["what"]])
        return {"ok": True}
    if kind == "tool_update_check":
        import time
        cache = os.path.join(ROOT, "agent", "update_check.json")
        if not a.get("force") and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 6 * 3600:
            return json.load(open(cache, encoding="utf-8"))
        import shutil
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        out = subprocess.run([uv, "run", "-q", "--no-project", "python", "-X", "utf8", os.path.join(ROOT, "tools", "update.py"), "--check"],
                             capture_output=True, text=True, encoding="utf-8", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        res = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"error": out.stderr[-300:]}
        if "error" not in res:  # don't remember a failed check (offline, release not out yet)
            json.dump(res, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
        return res
    if kind == "tool_update_apply":
        # visible window so the user can follow the update; it stops and restarts the bridge and the agent
        subprocess.Popen(["cmd.exe", "/c", "start", "Update", os.path.join(ROOT, "update.cmd")], cwd=ROOT)
        return {"ok": True}
    if kind in ("git_status", "git_commit", "release"):
        return git_action(kind, a)
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
