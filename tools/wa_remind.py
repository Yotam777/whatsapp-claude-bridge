import argparse, datetime as dt, os, sqlite3, sys
# Reminders for the owner (delivered by the agent into the control group, never to other people).
#   wa_remind.py add <utf8 text file> --at "2026-11-08 08:00"            time reminder
#   wa_remind.py add <utf8 text file> --in 90                            ... in 90 minutes
#   wa_remind.py add <utf8 text file> --noreply <chat_jid> --hours 4     remind only if nobody answered in that chat
#   wa_remind.py list | done <id> | cancel <id> | snooze <id> <minutes>
# Hebrew text must come from a UTF-8 file (never the command line).
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "agent"))
DB = os.path.join(HERE, "agent", "agent.db")
a = argparse.ArgumentParser()
a.add_argument("cmd"); a.add_argument("arg", nargs="*")
a.add_argument("--at"); a.add_argument("--in", dest="inmin", type=int)
a.add_argument("--noreply"); a.add_argument("--hours", type=float, default=4)
o = a.parse_args()
db = sqlite3.connect(DB, timeout=30)
db.execute("""CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT DEFAULT 'time', text TEXT,
    due TEXT, chat_jid TEXT, ref_ts TEXT, status TEXT DEFAULT 'active', created TEXT, fired TEXT)""")
now = dt.datetime.now().astimezone()
iso = lambda d: d.isoformat(timespec="seconds")
if o.cmd == "add":
    text = open(o.arg[0], encoding="utf-8").read().strip()
    if "??" in text or "�" in text:
        sys.exit("ERROR: text looks corrupted (write it to a UTF-8 file)")
    if o.noreply:
        kind, due, chat = "noreply", now + dt.timedelta(hours=o.hours), o.noreply
    elif o.inmin:
        kind, due, chat = "time", now + dt.timedelta(minutes=o.inmin), None
    elif o.at:
        kind, due, chat = "time", dt.datetime.fromisoformat(o.at).astimezone(), None
    else:
        sys.exit("need --at, --in or --noreply")
    cur = db.execute("INSERT INTO reminders (kind,text,due,chat_jid,ref_ts,created) VALUES (?,?,?,?,?,?)",
                     (kind, text, iso(due), chat, iso(now), iso(now)))
    db.commit(); print(f"reminder #{cur.lastrowid} due {iso(due)}")
elif o.cmd == "list":
    for r in db.execute("SELECT id,kind,due,status,substr(text,1,80),chat_jid FROM reminders WHERE status='active' ORDER BY due"):
        print(r)
elif o.cmd in ("done", "cancel"):
    db.execute("UPDATE reminders SET status=? WHERE id=?", ("done" if o.cmd == "done" else "cancelled", int(o.arg[0]))); db.commit(); print("ok")
elif o.cmd == "snooze":
    db.execute("UPDATE reminders SET status='active', due=? WHERE id=?", (iso(now + dt.timedelta(minutes=int(o.arg[1]))), int(o.arg[0]))); db.commit(); print("ok")
