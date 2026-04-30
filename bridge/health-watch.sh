#!/bin/bash
# Periodic chain self-heal + watch.
# Run by the com.james.buddyhealth launchd job every 4 hours.
#
# For each problem found, attempt one targeted remediation, recheck,
# and only fire a macOS notification if the problem persists (i.e. the
# user actually needs to act on it).  Silent on full success.
#
# Manual run:
#   ./health-watch.sh                 # standard cycle
#   ./health-watch.sh --force-notify  # notify even when everything is OK
#   ./health-watch.sh --no-fix        # detect-only, no remediation attempts

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/buddyhealth.log
NOW() { date "+%Y-%m-%d %H:%M:%S"; }
FORCE_NOTIFY=0
NO_FIX=0
for arg in "$@"; do
  case "$arg" in
    --force-notify) FORCE_NOTIFY=1 ;;
    --no-fix)       NO_FIX=1 ;;
  esac
done

log() { echo "[$(NOW)] $*" >> "$LOG"; }

notify() {
  local title="$1" body="$2"
  /usr/bin/osascript -e \
    "display notification \"${body//\"/\\\"}\" with title \"${title}\" sound name \"Funk\"" \
    >/dev/null 2>&1 || true
}

bridge_pid()        { /usr/bin/pgrep -f "bridge\.py" 2>/dev/null | head -1; }
bridge_status()     { /usr/bin/curl -s --max-time 3 http://127.0.0.1:5151/status; }
bridge_http_alive() { /usr/bin/curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5151/status 2>/dev/null | /usr/bin/grep -q '^200$'; }
ble_connected()     { bridge_status | /usr/bin/grep -q '"connected": true'; }
hook_wired()        { /usr/bin/grep -q "buddy_hook.py" "$HOME/.claude/settings.json" 2>/dev/null; }

# Optional remote sync — only runs when an existing SSH ControlMaster
# socket is active for the candy host (i.e. the user did `ssh candy`
# interactively in the last ControlPersist window, ~10 min).  Avoids
# auth failures when launchd's headless context can't reach Kerberos.
REMOTE_HOST="${BUDDY_REMOTE_HOST:-candy}"
ssh_master_alive() { /usr/bin/ssh -O check "$REMOTE_HOST" 2>/dev/null; }
remote_hash() {
  /usr/bin/ssh "$REMOTE_HOST" \
    "shasum -a 256 ~/state_hook.py 2>/dev/null || sha256sum ~/state_hook.py 2>/dev/null" \
    2>/dev/null | /usr/bin/awk '{print $1}'
}
local_hash() {
  /usr/bin/shasum -a 256 "$HERE/state_hook.py" | /usr/bin/awk '{print $1}'
}

sync_remote_if_drifted() {
  if ! ssh_master_alive; then
    log "  remote sync skipped — no SSH master to $REMOTE_HOST"
    return 1
  fi
  local rh lh
  rh=$(remote_hash)
  lh=$(local_hash)
  if [ -z "$rh" ]; then
    log "  remote sync skipped — couldn't read remote state_hook hash"
    return 1
  fi
  if [ "$rh" = "$lh" ]; then
    log "  remote state_hook in sync ($lh)"
    return 0
  fi
  log "  remote state_hook DRIFT — local=$lh remote=$rh, redeploying"
  if "$HERE/deploy-remote.sh" "$REMOTE_HOST" >> "$LOG" 2>&1; then
    log "  remote deploy succeeded"
    return 0
  else
    log "  remote deploy FAILED"
    return 1
  fi
}

# ── Remediation primitives ──────────────────────────────────────────────

heal_bridge_via_launchd() {
  log "  → kicking bridge via launchd (kill+respawn)"
  if pid=$(bridge_pid); then
    /bin/kill -KILL "$pid" 2>/dev/null || true
  fi
  # Enough headroom for: launchd ThrottleInterval=5s + bleak's 8s scan
  # cycle + connect handshake.  If Core2 is genuinely asleep nothing
  # this script can do helps.
  /bin/sleep 20
}

reinject_hook() {
  log "  → re-injecting PreToolUse hook into ~/.claude/settings.json"
  /usr/bin/python3 - <<'PY' 2>>"$LOG" || true
import json, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
if not p.exists():
    p.parent.mkdir(exist_ok=True)
    p.write_text("{}")
s = json.loads(p.read_text())
hooks = s.setdefault("hooks", {})
existing = hooks.get("PreToolUse", [])
already = any("buddy_hook.py" in h.get("command","")
              for entry in existing for h in entry.get("hooks", []))
if not already:
    existing.append({
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch",
        "hooks": [{
            "type": "command",
            "command": "/Users/bytedance/buddy/bridge/.venv/bin/python /Users/bytedance/buddy/bridge/buddy_hook.py",
        }],
    })
    hooks["PreToolUse"] = existing
    p.write_text(json.dumps(s, indent=2))
    print("re-injected")
else:
    print("already present")
PY
}

# ── Remote sync (best-effort, runs first so subsequent local checks
#    don't trip on a remote-only condition)
sync_remote_if_drifted || true

# ── Pass 1: detect ──────────────────────────────────────────────────────
problems=()
if ! bridge_pid >/dev/null;  then problems+=("bridge_dead"); fi
if ! bridge_http_alive;      then problems+=("http_dead"); fi
if ! ble_connected;          then problems+=("ble_down"); fi
if ! hook_wired;             then problems+=("hook_missing"); fi

if [ ${#problems[@]} -eq 0 ]; then
  log "OK — bridge running, BLE connected, hook wired"
  [ "$FORCE_NOTIFY" -eq 1 ] && notify "Buddy Bridge OK" "All checks passed at $(NOW)"
  exit 0
fi

log "DETECT — problems: ${problems[*]}"

# ── Pass 2: try to heal ─────────────────────────────────────────────────
if [ "$NO_FIX" -eq 0 ]; then
  for p in "${problems[@]}"; do
    case "$p" in
      bridge_dead|http_dead)
        # Bridge process either gone or wedged.  Either way, kill
        # (so launchd respawns it cleanly via KeepAlive).
        heal_bridge_via_launchd
        ;;
      ble_down)
        # Bridge is up but BLE not connected.  Let bridge's own
        # discover_and_hold loop retry — but if it's been stuck a while
        # the python BLE stack may need a kick.  Restart bridge.
        heal_bridge_via_launchd
        ;;
      hook_missing)
        reinject_hook
        ;;
    esac
  done
fi

# ── Pass 3: recheck after remediation ───────────────────────────────────
remaining=()
if ! bridge_pid >/dev/null;  then remaining+=("bridge still not running"); fi
if ! bridge_http_alive;      then remaining+=("HTTP still unreachable"); fi
if ! ble_connected;          then remaining+=("BLE still not connected (Core2 asleep / out of range?)"); fi
if ! hook_wired;             then remaining+=("hook still missing from settings.json"); fi

if [ ${#remaining[@]} -eq 0 ]; then
  log "HEALED — auto-fix succeeded, all checks now passing"
  notify "Buddy Bridge healed" "Auto-fixed: ${problems[*]}"
  exit 0
fi

# Still broken after self-heal — user needs to act.
body=$(printf '%s; ' "${remaining[@]}")
log "FAIL — after auto-heal, still: $body"
notify "Buddy Bridge needs attention" "$body"
exit 1
