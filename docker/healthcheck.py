"""Docker health check: healthy while the agent's heartbeat is fresh and the bridge answers and is connected."""
import datetime as dt
import json
import sqlite3
import sys
import urllib.request

APP = "/data/app"
try:
    db = sqlite3.connect(f"file:{APP}/agent/agent.db?mode=ro", uri=True, timeout=10)
    beat = db.execute("SELECT v FROM kv WHERE k='heartbeat'").fetchone()
    db.close()
    if not beat or dt.datetime.now() - dt.datetime.fromisoformat(beat[0]) > dt.timedelta(minutes=5):
        sys.exit("agent heartbeat is stale")
    token = open(f"{APP}/whatsapp-bridge/store/owner_token").read().strip()
    req = urllib.request.Request("http://127.0.0.1:8765/api/status", headers={"Authorization": "Bearer " + token})
    st = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15).read())
    if not st.get("connected"):
        sys.exit("bridge is not connected to WhatsApp")
except SystemExit:
    raise
except Exception as e:
    sys.exit(f"health check failed: {e}")
