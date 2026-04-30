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
        return "/"
    if name == os.path.basename(os.path.expanduser("~")):
        return "~"
    return name


def session_title(transcript_path: str) -> str:
    """Return the first real user prompt as a human-readable session
    name.  Claude Code itself doesn't assign session titles, but
    history.jsonl effectively uses the first prompt as the row's
    display value, so this matches the same mental model."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "user":
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                # User messages: either plain string, or a list of blocks.
                # Skip tool_result-only messages (those are internal echoes
                # back from the assistant's tool calls; not what the user
                # actually typed).
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text = c.get("text", "")
                            if text.strip():
                                break
                    else:
                        continue
                text = text.strip()
                if not text:
                    continue
                # First newline-delimited line, no leading whitespace
                first_line = text.splitlines()[0].strip()
                return first_line
    except Exception:
        pass
    return ""


def main():
    raw = sys.stdin.read()
    # Debug: append every Stop event we receive so we can audit whether
    # Claude Code is actually firing this hook (and what payload it sends).
    try:
        with open("/tmp/buddy-stop-events.log", "a") as f:
            f.write(f"{datetime.now().isoformat()} {raw[:500]}\n")
    except Exception:
        pass
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

    # Headline content for the firmware's full-screen completion banner.
    # The buddy renders this with the basic ASCII font (no CJK glyphs),
    # so we strip non-ASCII to avoid box/garbage characters.  Format:
    #
    #   "<project> <sid4> <tokens>"
    #
    # where <project> is the cwd basename, <sid4> is the first 4 hex
    # chars of the session_id (so multiple concurrent sessions in the
    # same project still distinguish), and <tokens> is the formatted
    # output count (e.g. 1.4M).
    cwd = event.get("cwd", "")
    proj_full = project_label(cwd)
    proj_ascii = "".join(c for c in proj_full if c.isascii() and c != " ")
    proj_ascii = proj_ascii[:14] or "?"
    sid4 = (event.get("session_id") or "")[:4] or "----"
    tok = fmt_tokens(sess_out)
    msg = f"{proj_ascii} {sid4} {tok}"[:46]

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

    # Append the POST attempt + outcome to the event log so we can audit
    # WHY the bridge call fails when fired by Claude Code (it works when
    # invoked manually — env / cwd difference suspected).
    def _logp(text: str):
        try:
            with open("/tmp/buddy-stop-events.log", "a") as f:
                f.write(text + "\n")
        except Exception:
            pass

    _logp(f"  posting to {BRIDGE_URL}: {json.dumps(body)[:200]}")
    try:
        req = urllib.request.Request(
            BRIDGE_URL,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # 5s gives plenty of headroom for a localhost POST.  Claude Code's
        # default hook timeout is generally 60s.
        with urllib.request.urlopen(req, timeout=5) as resp:
            _logp(f"  POST -> {resp.status} {resp.read()[:120].decode(errors='replace')}")
    except Exception as e:
        _logp(f"  POST FAIL: {type(e).__name__}: {e!r}")


if __name__ == "__main__":
    main()
