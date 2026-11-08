import datetime, json, os, sys, urllib.error, urllib.request
# Send a rich WhatsApp message through the local bridge (same draft/approval rules as text):
#   wa_rich.py <recipient jid> event    '{"name":"סיור גדר","start":"2026-11-10 08:00","end":"2026-11-10 10:00","address":"מחולה","text":"פרטים","guests":false}'
#   wa_rich.py <recipient jid> mention  '{"text":"נא לדווח ק\"מ היום","mentions":["972500000001"]}'
#   wa_rich.py <recipient jid> location '{"lat":32.36,"lng":35.51,"name":"שער צפוני","address":"מחולה"}'
# The JSON may also be a path to a UTF-8 file. Times: "YYYY-MM-DD HH:MM" (local) or unix seconds.
to, kind, arg = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.load(open(arg, encoding="utf-8")) if os.path.isfile(arg) else json.loads(arg)
for k in ("start", "end"):
    if isinstance(body.get(k), str):
        body[k] = int(datetime.datetime.strptime(body[k], "%Y-%m-%d %H:%M").timestamp())
body.update(jid=to, kind=kind)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()
req = urllib.request.Request("http://127.0.0.1:8765/api/send_rich", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    out = urllib.request.urlopen(req, timeout=180).read().decode()
except urllib.error.HTTPError as e:
    out = "HTTP %s %s" % (e.code, e.read().decode())
print(out)
try:
    with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as f:
        f.write("%s | Claude | rich=%s | to=%s | %r | result=%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), kind, to,
                                                                      json.dumps(body, ensure_ascii=False)[:120], out[:200].replace("\n", " ")))
except OSError:
    pass
