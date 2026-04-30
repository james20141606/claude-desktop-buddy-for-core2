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


def tally(path: str, today_str: str) -> tuple[int, int, int, int, int]:
    """Returns (session_output, today_output, all_time_output,
                 n_assistant_msgs, last_turn_output).

    last_turn_output is the sum of output_tokens for assistant
    messages emitted after the most recent "user" entry in the
    transcript — i.e. just what the assistant produced in the turn
    that just ended.  That's typically what the user means by
    "tokens this turn" instead of the much larger session total."""
    sess_out = today_out = all_out = n = 0
    last_turn_out = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = d.get("type")
                if t == "user":
                    # Only reset for REAL user prompts, not tool_result
                    # echoes back from the assistant's tool calls (which
                    # also have type=user).  Real prompts contain a
                    # text content block; tool results don't.
                    msg = d.get("message")
                    is_real_prompt = False
                    if isinstance(msg, dict):
                        c = msg.get("content")
                        if isinstance(c, str) and c.strip():
                            is_real_prompt = True
                        elif isinstance(c, list):
                            for blk in c:
                                if isinstance(blk, dict) and blk.get("type") == "text":
                                    if blk.get("text", "").strip():
                                        is_real_prompt = True
                                        break
                    if is_real_prompt:
                        last_turn_out = 0
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict) or "usage" not in msg:
                    continue
                u = msg["usage"]
                tok = u.get("output_tokens", 0)
                sess_out += tok
                all_out += tok
                last_turn_out += tok
                n += 1
                ts = d.get("timestamp", "")
                if ts.startswith(today_str):
                    today_out += tok
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return sess_out, today_out, all_out, n, last_turn_out


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


def _user_prompts(transcript_path: str):
    """Yield user-typed prompt strings from the transcript in order.
    Skips tool_result-only "user" messages (those are internal echoes
    back from assistant tool calls, not what the user actually typed)."""
    if not transcript_path or not os.path.exists(transcript_path):
        return
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
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            t = c.get("text", "")
                            if t.strip():
                                text = t
                                break
                    else:
                        continue
                text = text.strip()
                if text:
                    yield text
    except Exception:
        return


def session_title(transcript_path: str) -> str:
    """First real user prompt of the session — used as a session label."""
    for text in _user_prompts(transcript_path):
        return text.splitlines()[0].strip()
    return ""


def last_user_prompt(transcript_path: str) -> str:
    """Most recent user-typed prompt — what the assistant just finished
    answering.  Cleaner subject for the banner summary than the raw
    assistant response (which is often long, multilingual, code-fenced)."""
    last = ""
    for text in _user_prompts(transcript_path):
        last = text
    return last.splitlines()[0].strip() if last else ""


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
    sess_out = sess_n = last_turn_out = 0
    if transcript:
        sess_out, _, _, sess_n, last_turn_out = tally(transcript, today_str)

    # Today + all-time output across every Claude Code project on disk.
    # One pass, two accumulators; cheap even with hundreds of JSONLs.
    today_out_all = 0
    all_out_all = 0
    try:
        for f in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
            _, t, a, _, _ = tally(f, today_str)
            today_out_all += t
            all_out_all += a
    except Exception:
        pass

    # Multi-line full-screen banner content the firmware renders on
    # completion.  Lines are \n-separated, ASCII-only (the firmware
    # uses the default font with no CJK glyphs).
    #
    #   line 1: "<LOC>: <project> <sid4>"     where + which session
    #   line 2: "<task summary>"              what was just done
    #   line 3: "<tokens> · <Nmsgs>"          stats this turn
    cwd = event.get("cwd", "")
    proj_full = project_label(cwd)
    proj_ascii = "".join(c for c in proj_full if c.isascii() and c != " ")
    proj_ascii = proj_ascii[:14] or "?"
    sid4 = (event.get("session_id") or "")[:4] or "----"

    # LOCAL vs REMOTE: any non-Darwin uname implies the hook is running
    # on a server (we expect the byted workspace to be Linux).  Could
    # also peek at cwd starting with /home/ but uname is more reliable.
    import socket
    is_remote = os.uname().sysname != "Darwin"
    if is_remote:
        host = socket.gethostname().split(".")[0][:10]
        location = f"REMOTE {host}"
    else:
        location = "LOCAL"

    header = f"{location}: {proj_ascii} {sid4}"

    # Brief summary line.  Prefer the user's most recent prompt — it's
    # short, focused, and describes the task — over the assistant's
    # response (which is long, multilingual, and full of code fences
    # and ASCII art that don't survive an ASCII filter).  Fall back to
    # the assistant message if there's no usable user prompt.
    import re
    def _ascii_clean(text: str) -> str:
        # Drop code fences and inline backticks
        s = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        s = re.sub(r"~~~.*?~~~", " ", s,    flags=re.DOTALL)
        s = re.sub(r"`[^`]*`", " ", s)
        s = re.sub(r"^[\s>|]+", " ", s, flags=re.MULTILINE)
        for marker in ("**", "##", "#", ">", "*", "•", "—", "─", "│", "═"):
            s = s.replace(marker, "")
        s = "".join(c for c in s if c.isascii() and c.isprintable())
        return " ".join(s.split()).strip()

    def _looks_meaningful(s: str) -> bool:
        # An ASCII-only summary is "meaningful" if it has at least 12
        # chars AND contains at least one space (real sentences) AND
        # has at least one lowercase letter (filters "DONEDONE"-style
        # brand fragments leaked from CJK text).
        return (len(s) >= 12 and " " in s
                and any(c.islower() for c in s))

    user_prompt = last_user_prompt(transcript)
    summary = _ascii_clean(user_prompt)
    if not _looks_meaningful(summary):
        alt = _ascii_clean(event.get("last_assistant_message") or "")
        if _looks_meaningful(alt):
            summary = alt
    if not _looks_meaningful(summary):
        summary = f"finished turn {sess_n} in this session"
    summary = summary[:80]

    # Stats line shows THIS TURN's output tokens (what just happened),
    # not session total which can be many millions on a long convo.
    tok = fmt_tokens(last_turn_out)
    stats = f"{tok} this turn / {sess_n}msg total"

    banner_text = f"{header}\n{summary}\n{stats}"
    msg = f"{proj_ascii} {sid4} {tok}"[:46]

    body = {
        "tokens_today": today_out_all,
        "tokens_total": all_out_all,    # display-only on PET stats line
        "msg":          msg,            # short fallback for HUD strip
        "banner":       banner_text,    # multi-line full-screen overlay
        "entries":      [],             # force HUD into placeholder branch
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
