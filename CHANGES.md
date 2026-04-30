# What this fork adds over upstream

A working summary of every meaningful difference between
[`james20141606/claude-desktop-buddy-for-core2`](https://github.com/james20141606/claude-desktop-buddy-for-core2)
and the original
[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy).

The upstream firmware targets the M5StickC Plus and integrates only with
Claude Desktop's embedded Cowork sessions. Everything below is new.

---

## 1. Dual-board firmware (Core2 + StickC Plus from one source tree)

- `platformio.ini` defines two envs sharing a common `[env]`:
  - `m5stickc-plus` (default; matches upstream behaviour)
  - `m5stack-core2`
- `src/board_config.h` carries every board-specific constant — screen
  dimensions, pet geometry, scale factors, font sizes, peek shifts,
  clock layout, IMU axis sign, LED API.  Source files reference these
  macros instead of hard-coded numbers, so no `#ifdef` clutter
  scattered through the codebase.
- M5Unified replaces the M5StickCPlus library — same library handles
  both boards.

Build: `pio run -t upload` (StickC Plus) or
`pio run -e m5stack-core2 -t upload` (Core2).

## 2. Core2-specific UI work

- 240×320 portrait layout with the pet rendered at scale-3 ASCII
- `peekX` shift + narrow buddyTick clear so the PET-page header can
  sit beside the pet on the right side instead of stacking above it
- 18 ASCII pets all preserved; cat made the default species
- Fonts bumped across HUD / PET / INFO / clock for the bigger screen
- IMU-driven temperature display (MPU6886 die temp; cached 30 s)
- LittleFS auto-formats on first boot
- "James' Luna" identity locked at boot; bridge `owner` / `name`
  commands no-op'd to prevent paired desktops from overwriting it

## 3. Python bridge — Claude Code CLI integration

`bridge/bridge.py` replaces Claude Desktop's BLE role.

- Owns the BLE connection to the Core2 (`bleak`) and reconnects on drop
- Exposes a localhost HTTP API on port `5151`:
  - `POST /notify` — `{tool, hint, timeout?}` → `{decision: allow|deny}`
  - `POST /state`  — generic JSON passthrough (any data.h field)
  - `GET  /status` — `{connected, pending}`
- 15-second heartbeat keeps the device's "linked" state alive and
  syncs the on-device RTC so the clock face works after pairing
- Hello-ping on connect — sets the device msg to "Ready"
- Cancels pending HTTP callers on BLE drop so they don't hang forever
- launchd plist (`com.james.buddybridge.plist`) supervises the bridge
  and restarts on failure
- Health-watch script (`bridge/health-watch.sh`) runs every 4 hours
  and fires a macOS notification if any link in the chain breaks

## 4. Claude Code hooks

`bridge/buddy_hook.py` — `PreToolUse` hook (gates risky tools)
- Honours Claude Code's `permission_mode` field:
  - `bypassPermissions` / `plan` → silent passthrough
  - `acceptEdits` → silent for any tool (incl. Bash; override with
    `BUDDY_GATE_BASH_IN_AUTO=1`)
  - `default` → POSTs to `/notify` and waits for an A or B press
- A on the device approves, B denies; bridge's response shapes the
  hook's `permissionDecision` output so Claude Code skips its own
  in-terminal prompt entirely
- Fails open: bridge unreachable → exit 0 with no stdout, Claude
  Code falls back to its normal in-terminal prompt
- `BUDDY_DISABLE=1` env var → kill switch
- `BUDDY_DEBUG=1` → appends every event to
  `/tmp/buddy-hook-events.log` for auditing

`bridge/state_hook.py` — `Stop` hook (per-turn completion notification)
- Tallies tokens by scanning every `~/.claude/projects/*/*.jsonl`:
  - `tokens_total`: all-time `output_tokens`
  - `tokens_today`: today's `output_tokens` (local timezone)
- Builds a 3-line banner:
  - `<LOC>: <project> <sid4>`  ← where + which session
  - `<summary>`                  ← first ~80 ASCII chars of
                                    `last_assistant_message`,
                                    stripped of markdown / emoji
  - `<tokens> tokens / <N>msg`   ← per-turn stats
- LOCAL / REMOTE chosen via `os.uname()` (Darwin = local, anything
  else = remote with hostname)
- POSTs `{tokens_today, tokens_total, banner, msg, completed}` to the
  bridge

## 5. Completion notification on the device

When the bridge pushes `{"completed": true}`:

- Two-note up-chirp (2400 Hz → 3200 Hz) — distinct from the single
  approval-prompt beep
- Screen wakes from sleep
- Pet plays celebrate animation for 3 s
- **Full-screen green banner** overlays whatever page was active:
  - `DONE` header at size 5
  - 3 lines of structured info (LOC / summary / stats) at size 2
    with simple word-wrap
  - Countdown footer "press A to dismiss  Ns left"
  - 5-minute timeout, dismissable with A button
  - New completion replaces the current banner
- Rising-edge + msg-changed detection so back-to-back completions
  inside one heartbeat window each chirp; identical re-pushes inside
  a 1 s cooldown don't double-fire

## 6. Remote SSH deployment

`bridge/deploy-remote.sh <host>` (default `candy`):

- `scp`s both `buddy_hook.py` and `state_hook.py` to the remote
- Merges both hook entries into the remote `~/.claude/settings.json`
  (idempotent)
- Assumes an SSH alias is configured with `RemoteForward 5151
  localhost:5151` so the remote `localhost:5151` reaches the Mac's
  bridge.  README has a worked example using the byted dev workspace
  hostname.

## 7. Wire-format additions

The firmware's `data.h` `_applyJson` parses these new fields the
bridge can push (in addition to all upstream fields):

| Field           | Meaning                                            |
|-----------------|----------------------------------------------------|
| `tokens_total`  | All-time output tokens; pure display, no NVS write |
| `banner`        | Multi-line completion banner content (`\n`-sep)    |

Existing upstream fields (`prompt`, `running`, `waiting`, `total`,
`completed`, `msg`, `entries`, `tokens`, `tokens_today`, `time`) all
still work as before.

## 8. Defaults

- Default species: cat
- Default identity: "James' Luna"
- Default permission mode behaviour: bypass / acceptEdits both pass
  through to the device (see hook table above)
- Default banner duration: 5 minutes
- Default board target: `m5stickc-plus` (matches upstream)

---

## File map

```
src/board_config.h            ← per-board constants
src/data.h                    ← TamaState + JSON parsing
src/main.cpp                  ← UI, banner, button handling
src/buddy.cpp                 ← ASCII species rendering
src/buddies/*.cpp             ← 18 species
bridge/bridge.py              ← BLE central + HTTP server
bridge/buddy_hook.py          ← PreToolUse hook
bridge/state_hook.py          ← Stop hook
bridge/deploy-remote.sh       ← scp + ssh remote install
bridge/launch.sh              ← venv-aware launcher
bridge/health-watch.sh        ← 4-hour chain health check
bridge/com.james.buddybridge.plist     ← launchd supervisor
bridge/com.james.buddyhealth.plist     ← launchd health-check
```
