import json, os, sys, urllib.error, urllib.request
# send a WhatsApp message through the local bridge:
#   wa_send.py <recipient> <utf8 text file> [media path] [--quote=<message id>]
# In draft mode the bridge does not send to other people; it prepares a draft for the owner instead.
args = [a for a in sys.argv[1:] if not a.startswith("--quote=")]
quote = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--quote=")), "")
to, text_file = args[0], args[1]
body = {"recipient": to, "message": open(text_file, encoding="utf-8").read().strip()}
if len(args) > 2:
    body["media_path"] = args[2]
if quote:
    body["quoted_id"] = quote  # reply quoting that message (same chat)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()  # bridge API password
req = urllib.request.Request("http://127.0.0.1:8765/api/send", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    print(urllib.request.urlopen(req, timeout=180).read().decode())
except urllib.error.HTTPError as e:
    print(e.read().decode())
