#!/usr/bin/env python3
"""
Claude Code PreToolUse hook — asks the Core2 buddy for approval before
running a tool, via the local bridge.py HTTP endpoint.

Wire it up in .claude/settings.json (see settings.example.json).

Behavior:
  - On hook fire, reads Claude Code's tool-call event from stdin.
  - POSTs {tool, hint} to BUDDY_BRIDGE_URL (default localhost:5151).
  - Bridge displays prompt on Core2, blocks until A or B is pressed.
  - "allow" -> emit hookSpecificOutput allowing the tool without
              Claude Code's own permission prompt.
  - "deny"  -> emit hookSpecificOutput denying the tool.
  - Bridge unreachable / offline / timeout -> exit 0 silently so the
              tool falls back to Claude Code's normal permission flow.

Environment:
  BUDDY_BRIDGE_URL   POST endpoint  (default http://127.0.0.1:5151/notify)
  BUDDY_TIMEOUT      seconds to wait for a button press (default 60)
"""

import json
import os
import sys
import urllib.request
import urllib.error

BRIDGE_URL = os.environ.get("BUDDY_BRIDGE_URL", "http://127.0.0.1:5151/notify")
TIMEOUT = int(os.environ.get("BUDDY_TIMEOUT", "60"))


def _hint(tool: str, tool_input: dict) -> str:
    """Compress the tool input into a short hint readable on the device."""
    if tool == "Bash":
        return (tool_input.get("command") or "")[:80]
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return (tool_input.get("file_path") or "")[:80]
    if tool == "Read":
        return (tool_input.get("file_path") or "")[:80]
    if tool == "WebFetch":
        return (tool_input.get("url") or "")[:80]
    if tool == "Grep":
        return (tool_input.get("pattern") or "")[:80]
    return json.dumps(tool_input)[:80]


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    try:
        event = json.loads(raw)
    except Exception:
        sys.exit(0)

    tool = event.get("tool_name") or event.get("tool") or "tool"
    tool_input = event.get("tool_input") or {}
    hint = _hint(tool, tool_input)

    body = json.dumps({"tool": tool, "hint": hint, "timeout": TIMEOUT}).encode()
    req = urllib.request.Request(
        BRIDGE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT + 5) as resp:
            ans = json.loads(resp.read())
    except Exception as e:
        # Bridge unreachable: don't block the user, fall back to the
        # normal Claude Code permission flow.
        sys.stderr.write(f"buddy_hook: bridge unreachable ({e}); falling through\n")
        sys.exit(0)

    decision = ans.get("decision", "")
    if decision == "allow":
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "approved on buddy device",
            }
        }))
    elif decision == "deny":
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "denied on buddy device",
            }
        }))
    # decision="offline" or anything else: silent fallthrough, exit 0


if __name__ == "__main__":
    main()
