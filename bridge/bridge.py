#!/usr/bin/env python3
"""
buddy-bridge — BLE central + HTTP listener for Core2 buddy device.

Replaces Claude Desktop's BLE role: holds the connection to a Core2
running the claude-desktop-buddy-for-core2 firmware, and exposes a
local HTTP endpoint that Claude Code's PreToolUse hooks (local OR
remote-via-SSH-tunnel) can POST permission requests to.  The bridge
waits for the user to press A (allow) or B (deny) on the Core2, then
returns the decision to the hook so Claude Code can proceed or abort.

Setup
-----
  pip install -r requirements.txt   # bleak, aiohttp
  python3 bridge.py                 # foreground; ctrl-c to stop

First run will trigger macOS pairing — Core2 shows a 6-digit passkey,
macOS pops up a dialog asking for it.  Enter the passkey, pairing is
remembered for subsequent runs.

  > Important: disconnect Core2 from Claude Desktop first
  > (Developer → Hardware Buddy → Disconnect), or factory-reset Core2
  > (settings → reset → factory reset → tap twice).  Only one BLE
  > central can hold the connection at a time.

Protocol
--------
  POST /notify   {tool, hint, timeout?} -> {decision: "allow"|"deny"}
  GET  /status                        -> {connected, pending}

Environment
-----------
  BUDDY_BRIDGE_PORT  HTTP listener port (default 5151)
  BUDDY_DEVICE_NAME  exact BLE name to match (default: any "Claude-*")
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Optional, Dict

try:
    from bleak import BleakClient, BleakScanner
    from aiohttp import web
except ImportError:
    sys.stderr.write("missing deps; run: pip install bleak aiohttp\n")
    sys.exit(1)

# Nordic UART Service UUIDs — matches the Core2 firmware in src/ble_bridge.cpp
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # bridge writes
NUS_TX_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # Core2 notifies

DEVICE_NAME_PREFIX = "Claude-"
DEVICE_NAME_EXACT = os.environ.get("BUDDY_DEVICE_NAME", "")
HTTP_PORT = int(os.environ.get("BUDDY_BRIDGE_PORT", "5151"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")


class Bridge:
    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.connected = asyncio.Event()
        # promptId -> Future resolving with decision string
        self.pending: Dict[str, asyncio.Future] = {}
        self._rx_buf = b""
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def discover_and_hold(self):
        """Scan, connect, subscribe; reconnect on drop. Runs forever."""
        self._loop = asyncio.get_running_loop()
        while True:
            try:
                target = await self._scan()
                if not target:
                    log.info("no buddy found, retrying in 5s")
                    await asyncio.sleep(5)
                    continue
                log.info(f"found {target.name} ({target.address}), connecting...")
                async with BleakClient(target) as client:
                    self.client = client
                    await client.start_notify(NUS_TX_CHAR, self._on_notify)
                    log.info("connected, subscribed to TX notifications")
                    self.connected.set()
                    # Hello-ping so the device's "linked" state lights up.
                    # "Ready" reads cleaner than "buddy-bridge" if no
                    # state hook ever overrides it.  state_hook.py /
                    # /state HTTP can replace it any time.
                    import time as _t
                    _epoch = int(_t.time())
                    _tz = -_t.timezone if not _t.daylight else -_t.altzone
                    await self._send_raw({
                        "msg": "Ready",
                        "running": 0,
                        "waiting": 0,
                        "time": [_epoch, _tz],
                    })
                    while client.is_connected:
                        await asyncio.sleep(1)
                    log.info("BLE disconnected, will reconnect")
            except Exception as e:
                log.warning(f"BLE error: {e!r}")
            finally:
                self.connected.clear()
                self.client = None
                # Cancel pending so HTTP callers don't hang forever on a
                # disconnect mid-prompt.
                for pid, fut in list(self.pending.items()):
                    if not fut.done():
                        fut.set_result("deny")
                self.pending.clear()
                await asyncio.sleep(2)

    async def _scan(self):
        log.info("scanning for buddy device...")
        devices = await BleakScanner.discover(timeout=8.0)
        if DEVICE_NAME_EXACT:
            for d in devices:
                if d.name == DEVICE_NAME_EXACT:
                    return d
            return None
        for d in devices:
            if d.name and d.name.startswith(DEVICE_NAME_PREFIX):
                return d
        return None

    def _on_notify(self, _char, data: bytearray):
        """Buffer line-delimited JSON. Resolve pending futures on match."""
        self._rx_buf += bytes(data)
        while b"\n" in self._rx_buf:
            line, _, self._rx_buf = self._rx_buf.partition(b"\n")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode())
            except Exception:
                log.warning(f"non-json from core2: {line!r}")
                continue
            if msg.get("cmd") == "permission":
                pid = msg.get("id")
                decision = msg.get("decision", "deny")
                fut = self.pending.pop(pid, None)
                if fut and not fut.done():
                    fut.set_result(decision)
                else:
                    log.info(f"got late response for {pid}: {decision}")
            else:
                log.debug(f"unhandled msg: {msg}")

    async def _send_raw(self, obj: dict):
        if not self.client or not self.client.is_connected:
            return
        payload = (json.dumps(obj) + "\n").encode()
        # MTU − 3; M5Unified negotiates ~185 on macOS, we cap at 180.
        chunk = 180
        for i in range(0, len(payload), chunk):
            await self.client.write_gatt_char(NUS_RX_CHAR, payload[i:i+chunk],
                                              response=False)

    async def request_permission(self, tool: str, hint: str,
                                 timeout: float = 60.0) -> str:
        if not self.connected.is_set():
            return "offline"
        pid = uuid.uuid4().hex[:16]
        fut = asyncio.get_running_loop().create_future()
        self.pending[pid] = fut
        prompt = {
            "id": pid,
            "tool": (tool or "tool")[:20],
            "hint": (hint or "")[:80],
        }
        await self._send_raw({"prompt": prompt})
        log.info(f"prompt[{pid}] tool={tool!r} hint={hint!r}")
        try:
            decision_raw = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending.pop(pid, None)
            log.warning(f"prompt[{pid}] timeout")
            # Clear the prompt on screen
            await self._send_raw({"prompt": None})
            return "deny"
        # Core2 firmware emits "once" on A press, "deny" on B press
        return "allow" if decision_raw in ("once", "always", "allow") else "deny"


bridge = Bridge()


async def handle_notify(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    tool = body.get("tool", "tool")
    hint = body.get("hint", "")
    timeout = float(body.get("timeout", 60))
    decision = await bridge.request_permission(tool, hint, timeout)
    return web.json_response({"decision": decision})


async def handle_state(request: web.Request):
    """Generic passthrough: forward arbitrary JSON state to Core2.
    Used by the Stop hook to push token counts, session info, status
    messages — anything in the firmware's data.h JSON schema.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not bridge.connected.is_set():
        return web.json_response({"ok": False, "error": "offline"})
    await bridge._send_raw(body)
    return web.json_response({"ok": True})


async def handle_status(_request):
    return web.json_response({
        "connected": bridge.connected.is_set(),
        "pending": list(bridge.pending.keys()),
    })


async def heartbeat_loop():
    """Keep the device's 'linked' indicator alive. data.h's dataConnected()
    times out after 30s without traffic; send a small ping every 15s.
    Also pushes wall-clock time so the firmware's clock stays in sync."""
    import time
    while True:
        await asyncio.sleep(15)
        if not bridge.connected.is_set():
            continue
        # Local time (epoch) + tz offset in seconds.  data.h decodes this
        # as {"time":[epoch,tz_offset]} and calls M5.Rtc.setDateTime().
        epoch = int(time.time())
        tz_offset = -time.timezone if not time.daylight else -time.altzone
        try:
            await bridge._send_raw({"time": [epoch, tz_offset]})
        except Exception as e:
            log.warning(f"heartbeat failed: {e!r}")


async def main():
    app = web.Application()
    app.router.add_post("/notify", handle_notify)
    app.router.add_post("/state", handle_state)
    app.router.add_get("/status", handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", HTTP_PORT)
    await site.start()
    log.info(f"http listening on http://127.0.0.1:{HTTP_PORT}")
    log.info(f"  POST /notify  body={{tool, hint, timeout?}}  -> {{decision}}")
    log.info(f"  POST /state   body={{any data.h fields}}     -> {{ok}}")
    log.info(f"  GET  /status                                  -> {{connected, pending}}")
    asyncio.create_task(bridge.discover_and_hold())
    asyncio.create_task(heartbeat_loop())
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
