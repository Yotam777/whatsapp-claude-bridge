"""Call a bridge admin endpoint safely (UTF-8 JSON from a file or stdin).
Usage: wa_admin.py /api/set_group payload.json   (or '-' for stdin)
Never pass Hebrew through a shell command line/curl: the console code page turns it into '????'.
Refuses payloads whose text fields contain '?' runs or U+FFFD (already-corrupted text)."""
import json, os, re, sys, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()
ep, src = sys.argv[1], sys.argv[2]
raw = sys.stdin.buffer.read() if src == "-" else open(src, "rb").read()
data = json.loads(raw.decode("utf-8"))
for k, v in data.items():
    if isinstance(v, str) and (re.search(r"\?{2,}", v) or "�" in v):
        sys.exit(f"refused: field {k!r} looks corrupted (??? / U+FFFD)")
req = urllib.request.Request("http://127.0.0.1:8765" + ep, json.dumps(data).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    print(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode("utf-8", "replace")); sys.exit(1)
