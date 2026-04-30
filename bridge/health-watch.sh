#!/bin/bash
# Periodic chain health check.
# Run by the com.james.buddyhealth launchd job every 4 hours.
# Pops a macOS notification + appends to /tmp/buddyhealth.log when
# something fails, otherwise stays silent.
#
# Manual run for testing:
#   ./health-watch.sh
#   ./health-watch.sh --force-notify    (notify even on success)

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/buddyhealth.log
NOW=$(date "+%Y-%m-%d %H:%M:%S")
FORCE=0
[ "$1" = "--force-notify" ] && FORCE=1

log() { echo "[$NOW] $*" >> "$LOG"; }

# osascript notification — pops up in macOS Notification Center.
notify() {
  local title="$1" body="$2"
  /usr/bin/osascript -e \
    "display notification \"${body//\"/\\\"}\" with title \"${title}\" sound name \"Funk\"" \
    >/dev/null 2>&1 || true
}

problems=()

# 1. Bridge process alive?
if ! /usr/bin/pgrep -f "bridge\.py" >/dev/null 2>&1; then
  problems+=("bridge process not running (launchd should restart it)")
fi

# 2. Bridge HTTP responsive?
if ! status=$(/usr/bin/curl -s --max-time 3 http://127.0.0.1:5151/status); then
  problems+=("bridge HTTP unreachable on :5151")
fi

# 3. BLE connected to Core2?
if [ -n "$status" ] && ! echo "$status" | /usr/bin/grep -q '"connected": true'; then
  problems+=("bridge up but BLE not connected to Core2")
fi

# 4. PreToolUse hook still wired?
if ! /usr/bin/grep -q "buddy_hook.py" "$HOME/.claude/settings.json" 2>/dev/null; then
  problems+=("PreToolUse hook missing from ~/.claude/settings.json")
fi

# Report
if [ ${#problems[@]} -eq 0 ]; then
  log "OK — bridge running, BLE connected, hook wired"
  if [ "$FORCE" -eq 1 ]; then
    notify "Buddy Bridge OK" "All checks passed at $NOW"
  fi
  exit 0
fi

# Failure
body=$(printf '%s; ' "${problems[@]}")
log "FAIL — $body"
notify "Buddy Bridge problem" "$body"
exit 1
