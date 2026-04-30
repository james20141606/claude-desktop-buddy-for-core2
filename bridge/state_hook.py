#!/usr/bin/env python3
"""
Claude Code Stop hook — pushes per-session and today's token totals to
the buddy bridge so the device's PET stats page shows real activity.

Runs at the end of every assistant turn.  Reads:
  - The transcript_path passed in by Claude Code (current session only)
  - All ~/.claude/projects/<dir>/<sessionid>.jsonl on disk for "today"
    cumulative across every project

POSTs to bridge:
  {
    "tokens_today": <today output_tokens across all projects>,
    "msg":          "<short status: msgs / tools this session>",
    "running":      0/1,
    "completed":    true,
  }

Fails open: any exception → exit 0 silent so it never blocks Claude Code.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import glob
from datetime import datetime, timezone, timedelta

BRIDGE_URL = os.environ.get("BUDDY_BRIDGE_URL", "http://127.0.0.1:5151/state")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo


def tally(path: str, today_str: str) -> tuple[int, int, int, int]:
    """Returns (session_output, today_output, all_time_output, n_assistant_msgs)."""
    sess_out = today_out = all_out = n = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict) or "usage" not in msg:
                    continue
                u = msg["usage"]
                tok = u.get("output_tokens", 0)
                sess_out += tok
                all_out += tok
                n += 1
                ts = d.get("timestamp", "")
                if ts.startswith(today_str):
                    today_out += tok
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return sess_out, today_out, all_out, n


def fmt_tokens(n: int) -> str:
    """Human-readable token count: 1234 -> '1.2K', 1500000 -> '1.5M'."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def project_label(cwd: str) -> str:
    """Short identifier for the project the assistant just finished a turn
    in.  basename of cwd, falling back to home shorthand."""
    if not cwd:
        return "?"
    name = os.path.basename(cwd.rstrip("/"))
    if not name:
        # cwd was '/' or similar
        return "/"
    if name == os.path.basename(os.path.expanduser("~")):
        return "~"
    return name


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = {}

    # Today's date in the LOCAL timezone — matches how the user thinks
    # about "today" rather than UTC midnight.
    now_local = datetime.now(LOCAL_TZ)
    today_str = now_local.strftime("%Y-%m-%d")

    # Current session breakdown
    transcript = event.get("transcript_path")
    sess_out = sess_n = 0
    if transcript:
        sess_out, _, _, sess_n = tally(transcript, today_str)

    # Today + all-time output across every Claude Code project on disk.
    # One pass, two accumulators; cheap even with hundreds of JSONLs.
    today_out_all = 0
    all_out_all = 0
    try:
        for f in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
            _, t, a, _ = tally(f, today_str)
            today_out_all += t
            all_out_all += a
    except Exception:
        pass

    # Headline: which project just finished + how many tokens this turn.
    # data.h caps msg at 23 chars; project name truncated to fit.
    proj = project_label(event.get("cwd", ""))[:14]
    tok = fmt_tokens(sess_out)
    msg = f"done {proj} {tok}"[:23]

    body = {
        "tokens_today": today_out_all,
        "tokens_total": all_out_all,    # display-only on PET stats line
        "msg":          msg,
        "entries":      [],             # force HUD into placeholder branch
                                        # so the headline msg is the big
                                        # auto-fit text on screen
        "running":      0,              # turn just ended
        "completed":    True,           # triggers the celebrate animation
    }

    try:
        req = urllib.request.Request(
            BRIDGE_URL,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        # Bridge offline? Don't bother Claude Code about it.
        pass


if __name__ == "__main__":
    main()
