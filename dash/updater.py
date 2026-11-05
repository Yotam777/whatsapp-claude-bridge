"""Updates for the desktop dashboard.

Release folder (default <tool>/releases, or "update_dir" in the dashboard settings) holds latest.json:
{"version": "1.0.1", "file": "WhatsAppDash-1.0.1.exe", "notes": "..."}; a GitHub release URL can be used the same way
later ("update_url"). apply() copies the new exe next to the running one and restarts through a small batch file.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from dash.server import ROOT, VERSION


def _settings():
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WhatsAppDash", "settings.json")
    try:
        return json.load(open(p, encoding="utf-8-sig"))
    except Exception:
        return {}


def _ver(v):
    return tuple(int(x) for x in v.split("."))


def _latest():
    """Newest of the GitHub release and the local release folder."""
    a, b = _latest_url(), _latest_dir()
    best = [x for x in (a, b) if x[0]]
    return max(best, key=lambda x: _ver(x[0]["version"])) if best else (None, None)


def _latest_url():
    s = _settings()
    s.setdefault("update_url", "https://github.com/WallaBoohi/whatsapp-claude-bridge/releases/latest/download/latest.json")
    try:
        return json.loads(urllib.request.urlopen(s["update_url"], timeout=8).read().decode()), "url"
    except Exception:
        return None, None


def _latest_dir():
    s = _settings()
    d = s.get("update_dir") or os.path.join(ROOT, "releases")
    try:
        return json.load(open(os.path.join(d, "latest.json"), encoding="utf-8")), d
    except Exception:
        return None, d


def check():
    info, _ = _latest()
    if not info:
        return {"available": False, "message": "לא נמצא מקור עדכונים"}
    if _ver(info["version"]) > _ver(VERSION):
        return {"available": True, "version": info["version"], "notes": info.get("notes", "")}
    return {"available": False, "message": f"הגרסה עדכנית ({VERSION})"}


def apply():
    info, src = _latest()
    if not info or _ver(info["version"]) <= _ver(VERSION):
        return {"error": "אין עדכון"}
    if not getattr(sys, "frozen", False):
        return {"error": "העדכון זמין רק באפליקציה המותקנת"}
    exe = sys.executable
    new = exe + ".new"
    if src == "url":
        urllib.request.urlretrieve(info["download"], new)
    else:
        shutil.copy(os.path.join(src, info["file"]), new)
    bat = os.path.join(os.path.dirname(exe), "update.cmd")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\ntimeout /t 2 /nobreak > nul\r\nmove /y "{new}" "{exe}" > nul\r\nstart "" "{exe}"\r\ndel "%~f0"\r\n')
    subprocess.Popen(["cmd.exe", "/c", bat], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os._exit(0)
