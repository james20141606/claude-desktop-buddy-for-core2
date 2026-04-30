# Bridge — route Claude Code permission prompts to Core2

`bridge.py` replaces Claude Desktop's BLE role.  It connects to the
Core2 buddy device and exposes an HTTP endpoint on `localhost:5151`.
A small Claude Code hook (`buddy_hook.py`) POSTs every `PreToolUse`
event there, the bridge displays the prompt on Core2, and the user's
A (approve) / B (deny) press is returned to the hook to gate the tool.

Works for both:

- **Local** Claude Code on the same Mac
- **Remote** Claude Code on a server you SSH into, by reverse-tunnelling
  port `5151` back to the Mac so the remote `localhost:5151` hits the
  bridge

## Setup

### 1. Disconnect Claude Desktop from Core2

Only one BLE central can own the connection at a time.  In Claude
Desktop → **Developer → Hardware Buddy → Disconnect**, or factory-reset
the Core2 (settings → reset → factory reset → tap twice).

### 2. Install Python deps

```bash
cd bridge/
pip install -r requirements.txt
```

`bleak` is the cross-platform BLE library; `aiohttp` is the HTTP server.

### 3. Run the bridge

```bash
python3 bridge.py
```

First connect, macOS will pop a "Bluetooth pairing request" dialog
showing a 6-digit number — that number must match the passkey on the
Core2 screen.  Type it into the dialog.  After this, the pairing is
stored and reconnects are silent.

Logs:

```
2026-04-29 16:05:01 INFO scanning for buddy device...
2026-04-29 16:05:09 INFO found Claude-A1B2 (...), connecting...
2026-04-29 16:05:11 INFO connected, subscribed to TX notifications
2026-04-29 16:05:11 INFO http listening on http://127.0.0.1:5151
```

Verify with:

```bash
curl http://127.0.0.1:5151/status
# {"connected": true, "pending": []}
```

### 4. Wire up the Claude Code hook

Edit the path in `settings.example.json` to point at where you cloned
this repo, then drop it into one of:

- `~/.claude/settings.json` (global)
- `<project>/.claude/settings.json` (per-project)

Or merge with your existing settings:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/bridge/buddy_hook.py"
          }
        ]
      }
    ]
  }
}
```

The `matcher` regex picks which tools require buddy approval.  Read /
Glob / Grep / TodoWrite are usually safe to skip.

### 5. Test

```bash
cd /tmp && claude
```

Ask it to run `ls /tmp` (a `Bash` tool call).  Core2 should show:

```
approve? 0s
Bash
ls /tmp
```

Press **A** on the bottom-left touch button → tool runs.  Press **B**
on the bottom-middle → Claude Code aborts that call.

## Remote SSH usage

On the **remote** server, deploy `buddy_hook.py` (anywhere on PATH or
in a known absolute location), then SSH in with a reverse tunnel that
exposes the Mac's bridge port back to the server's localhost:

```bash
# Example for the byted dev workspace
ssh -R 5151:localhost:5151 <your-uid>@<your-server-host>
```

Now on the server, anything POSTing to `http://localhost:5151` reaches
the bridge running on your Mac.  On the server, write a similar
`.claude/settings.json` (with the path adjusted to where you put
`buddy_hook.py` on the server):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch",
        "hooks": [
          { "type": "command", "command": "python3 ~/buddy_hook.py" }
        ]
      }
    ]
  }
}
```

A one-shot deploy from your Mac:

```bash
scp bridge/buddy_hook.py <your-uid>@<your-server-host>:~/
ssh <your-uid>@<your-server-host> \
    "mkdir -p ~/.claude && cat > ~/.claude/settings.json" \
    < bridge/settings.example.json
```

Then make the SSH-with-tunnel command an alias in `~/.ssh/config` on
your Mac so you don't forget the `-R`:

```sshconfig
Host candy
  HostName <your-server-host>
  User <your-uid>
  RemoteForward 5151 localhost:5151
```

Then just `ssh candy`, the tunnel comes for free.

If the bridge is unreachable (e.g. Mac asleep, you forgot the tunnel,
Bluetooth off), the hook silently falls back to Claude Code's normal
in-terminal permission prompt — it won't block your CLI.

## Run the bridge as a launchd service (optional)

So you don't have to remember to start it.  `~/Library/LaunchAgents/com.james.buddybridge.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.james.buddybridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/bytedance/buddy/bridge/bridge.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/buddybridge.out</string>
  <key>StandardErrorPath</key><string>/tmp/buddybridge.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.james.buddybridge.plist
```

## Caveats

- **Single-buddy assumption**: the bridge connects to the first
  `Claude-XXXX` it sees.  Set `BUDDY_DEVICE_NAME=Claude-A1B2` env var to
  pin to a specific device when you have several.
- **Pairing per host**: if you move the bridge to a different machine,
  you'll re-pair on first connect.  macOS keeps the bond per-account.
- **Read tools fall through**: hooks are configured to fire on
  Bash/Edit/Write/MultiEdit/NotebookEdit/WebFetch by default.  Add or
  remove tool names in the `matcher` regex to taste.
- **Failure mode is permissive, not strict**: bridge unreachable →
  hook exits 0 → Claude Code uses its in-terminal prompt.  This avoids
  bricking your CLI when the Mac is asleep / Bluetooth is off.  If you
  want strict deny-on-failure, change the `except` block in
  `buddy_hook.py` to emit `{"permissionDecision": "deny", ...}`.
