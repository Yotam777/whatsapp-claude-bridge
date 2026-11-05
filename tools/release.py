"""Release a new version of the WhatsApp ↔ Claude tool (maintainer).

  release.cmd 1.1.0 "מה השתנה / what changed"            run all checks, commit, tag and push → GitHub builds and publishes
  release.cmd 1.1.0 "…" --dry-run                         everything except commit/tag/push

1. bumps the version everywhere: VERSION, dash/VERSION, .claude-plugin/plugin.json, agent/pyproject.toml
2. adds the notes on top of CHANGELOG.md
3. checks: Go vet + tests, agent tests, MCP import, `claude plugin validate --strict`, no personal data in tracked files
4. commits "Release vX.Y.Z", tags vX.Y.Z, pushes main + tag (GitHub Actions builds the bridge and the dashboard
   and publishes the release; installed tools see it in the dashboard and get a WhatsApp notice the next morning)
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, cwd=ROOT, env=None):
    print("→", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, text=True, encoding="utf-8", capture_output=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit(f"✖ נכשל: {' '.join(cmd)}")
    return r.stdout


def personal_data_scan(patterns):
    files = run(["git", "ls-files"]).split()
    hits = []
    for f in files:
        if f.endswith((".woff2", ".ico", ".png", ".exe", ".sum", ".lock")) or "chart.umd" in f:
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        hits += [f"{f}: {p}" for p in patterns if p and p in text]
    return hits


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("שימוש: release.cmd X.Y.Z \"מה השתנה\" [--dry-run]")
    ver, notes, dry = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""), "--dry-run" in sys.argv
    if run(["git", "status", "--porcelain", "--untracked-files=no"]).strip() and not dry:
        sys.exit("✖ יש שינויים שלא נשמרו (commit) — שמור אותם קודם.")

    # 1. version everywhere
    open(os.path.join(ROOT, "VERSION"), "w").write(ver + "\n")
    open(os.path.join(ROOT, "dash", "VERSION"), "w").write(ver + "\n")
    pj = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    p = json.load(open(pj, encoding="utf-8")); p["version"] = ver
    json.dump(p, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    pp = os.path.join(ROOT, "agent", "pyproject.toml")
    open(pp, "w", encoding="utf-8").write(re.sub(r'(?m)^version = ".*"$', f'version = "{ver}"', open(pp, encoding="utf-8").read(), count=1))

    # 2. changelog
    cl = os.path.join(ROOT, "CHANGELOG.md")
    text = open(cl, encoding="utf-8").read()
    entry = f"## {ver} — {dt.date.today().isoformat()}\n{notes or '—'}\n\n"
    if f"## {ver} " not in text:
        text = text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
        open(cl, "w", encoding="utf-8").write(text)

    # 3. checks
    go = shutil.which("go") or "C:/Program Files/Go/bin/go.exe"
    env = {"CGO_ENABLED": "1", "PATH": "C:/msys64/ucrt64/bin;" + os.environ["PATH"]}
    run([go, "vet", "."], cwd=os.path.join(ROOT, "whatsapp-bridge"), env=env)
    run([go, "test", "./..."], cwd=os.path.join(ROOT, "whatsapp-bridge"), env=env)
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
    run([uv, "run", "-q", "--no-sync", "--group", "dev", "pytest", "-q", "tests"], cwd=os.path.join(ROOT, "agent"))
    run([uv, "run", "-q", "python", "-c", "import main, extras"], cwd=os.path.join(ROOT, "whatsapp-mcp-server"))
    claude = shutil.which("claude")
    if claude:
        run([claude, "plugin", "validate", "--strict", "."])
    cfg = {}
    try:
        cfg = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
    except Exception:
        pass
    personal = [cfg.get("claude_group", ""), os.path.expanduser("~").replace("\\", "/"), os.path.expanduser("~")]
    try:
        pol = json.load(open(os.path.join(ROOT, "whatsapp-bridge", "store", "send_policy.json"), encoding="utf-8"))
        personal += [o.split("@")[0] for o in pol.get("owner", [])]
    except Exception:
        pass
    hits = personal_data_scan([x for x in personal if len(x) > 6])
    if hits:
        sys.exit("✖ נמצא מידע אישי בקבצים שעולים לגיט:\n" + "\n".join(hits))
    print("✔ כל הבדיקות עברו")
    if dry:
        print("(dry run — לא נשמר ולא פורסם)")
        return

    # 4. commit, tag, push
    run(["git", "add", "VERSION", "dash/VERSION", ".claude-plugin/plugin.json", "agent/pyproject.toml", "CHANGELOG.md"])
    run(["git", "commit", "-m", f"Release v{ver}\n\n{notes}"])
    run(["git", "tag", "-a", f"v{ver}", "-m", f"v{ver}"])
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    run(["git", "push", "origin", f"{branch}:main"])
    run(["git", "push", "origin", f"v{ver}"])
    print(f"✔ v{ver} פורסמה. GitHub בונה את הקבצים: https://github.com/WallaBoohi/whatsapp-claude-bridge/actions")


if __name__ == "__main__":
    main()
