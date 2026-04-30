#!/bin/bash
# Deploy buddy_hook.py to a remote host and write its ~/.claude/settings.json.
# Usage:   ./deploy-remote.sh [ssh-host]    (default: candy)
#
# Assumes the SSH alias is configured (see ~/.ssh/config "Host candy").
# Idempotent: re-running won't duplicate hooks.

set -e
HOST="${1:-candy}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo ">> copying buddy_hook.py + state_hook.py to $HOST:~/"
scp -q "$HERE/buddy_hook.py" "$HOST:buddy_hook.py"
scp -q "$HERE/state_hook.py" "$HOST:state_hook.py"

echo ">> updating ~/.claude/settings.json on $HOST"
ssh "$HOST" python3 - <<'PYEOF'
import json, os, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
p.parent.mkdir(exist_ok=True)
s = json.loads(p.read_text()) if p.exists() else {}
hooks = s.setdefault("hooks", {})
home = pathlib.Path.home()

# PreToolUse — gates risky tools through the buddy device (gate is no-op
# in bypass / plan / acceptEdits modes per the hook's own logic)
pre = hooks.setdefault("PreToolUse", [])
if not any("buddy_hook.py" in h.get("command", "")
           for e in pre for h in e.get("hooks", [])):
    pre.append({
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|Read",
        "hooks": [{
            "type": "command",
            "command": f"python3 {home / 'buddy_hook.py'}",
        }],
    })
    print("added PreToolUse hook")

# Stop — fires after every assistant turn regardless of mode; pushes
# tokens and a "done <session> <Ntk>" headline that wakes the device
# back home so the user knows this remote session just finished.
stop = hooks.setdefault("Stop", [])
if not any("state_hook.py" in h.get("command", "")
           for e in stop for h in e.get("hooks", [])):
    stop.append({
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {home / 'state_hook.py'}",
        }],
    })
    print("added Stop hook")

p.write_text(json.dumps(s, indent=2))
print("settings written to", p)
PYEOF

echo ">> done. test:   ssh $HOST 'cat ~/.claude/settings.json'"
echo ">> remember: ssh in with the 'candy' alias so RemoteForward 5151 is active"
