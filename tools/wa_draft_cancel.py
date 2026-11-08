"""Cancel pending drafts on the owner's instruction:  wa_draft_cancel.py 24 25 30-42
Cancel only: this tool can never approve or send a draft (approval stays with the owner: ✅ on the card or the dashboard).
Each cancelled draft gets the usual "הטיוטה ל... בוטלה" note in the control group."""
import json, os, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()


def ids(args):
    for a in args:
        if "-" in a:
            lo, hi = (int(x) for x in a.split("-", 1))
            yield from range(lo, hi + 1)
        else:
            yield int(a)


def cancel(i):
    body = json.dumps({"id": i, "approve": False}).encode()  # approve is always False here
    req = urllib.request.Request("http://127.0.0.1:8765/api/draft_decide", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")
    except Exception as e:
        return str(e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    import datetime, time
    log = os.path.join(ROOT, "agent", "actions.log")
    for i in ids(sys.argv[1:]):
        res = cancel(i)
        print(i, res.strip()[:120])
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} Claude: draft cancel #{i} -> {res.strip()[:80]}\n")
        time.sleep(1.5)  # the control-group notes are paced by the bridge anyway
