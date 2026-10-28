"""Hardware session: handshake, request/ack, heartbeat, telemetry, authorization gate."""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..models import TelemetryRecord
from .protocol import ACTUATING, Command, ProtocolError, Reply, decode
from .transport import Transport, TransportClosed

HEARTBEAT_S = 2.0
HEARTBEAT_MISS_LIMIT = 3
REQUEST_TIMEOUT_S = 2.0


class HardwareError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class HardwareSession:
    def __init__(self, transport: Transport, *, heartbeat_s: float = HEARTBEAT_S):
        self.transport = transport
        self.heartbeat_s = heartbeat_s
        self.status = "disconnected"  # disconnected | connected | lost | unsupported
        self.info: dict[str, Any] = {}
        self.last_heartbeat: datetime | None = None
        self.telemetry: deque[TelemetryRecord] = deque(maxlen=500)
        self.latencies_ms: list[float] = []
        self.commands_sent: list[dict] = []  # audit log
        self._pending: dict[str, asyncio.Future[Reply]] = {}
        self._tasks: list[asyncio.Task] = []
        self._subscribers: list[asyncio.Queue] = []
        self._authorized: dict | None = None
        self._misses = 0
        self.on_latency = None  # optional callback(ms) for metrics

    @property
    def simulated(self) -> bool:
        return bool(self.transport.simulated or self.info.get("simulated"))

    # ---- lifecycle -----------------------------------------------------------------
    async def connect(self) -> dict:
        await self.transport.open()
        self._tasks.append(asyncio.create_task(self._reader()))
        try:
            reply = await self._request("hello", {})
        except HardwareError:
            await self.disconnect()
            self.status = "unsupported"
            raise HardwareError("handshake_failed", "Device did not answer the BuildLoop handshake. Is BuildLoop firmware flashed?")
        fw = str(reply.data.get("fw", ""))
        if not fw.startswith("buildloop"):
            await self.disconnect()
            self.status = "unsupported"
            raise HardwareError("unsupported_device", f"Unsupported firmware '{fw}'.")
        self.info = dict(reply.data)
        self.status = "connected"
        self.last_heartbeat = datetime.now().astimezone()
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        return self.info

    async def disconnect(self) -> None:
        if self.status == "connected":
            with contextlib.suppress(Exception):
                await self.stop()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        with contextlib.suppress(Exception):
            await self.transport.close()
        for f in self._pending.values():
            if not f.done():
                f.set_exception(HardwareError("disconnected", "Session closed"))
        self._pending.clear()
        self.status = "disconnected"
        self._authorized = None

    # ---- authorization ------------------------------------------------------------
    def authorize(self, design_id: str, led_pin: int) -> None:
        """Called only by trusted code after validation + user approval."""
        self._authorized = {"design_id": design_id, "led_pin": led_pin}

    def revoke(self) -> None:
        self._authorized = None

    @property
    def authorized_design(self) -> str | None:
        return self._authorized["design_id"] if self._authorized else None

    # ---- commands -----------------------------------------------------------------
    async def _request(self, cmd: str, args: dict, timeout: float = REQUEST_TIMEOUT_S) -> Reply:
        c = Command(id=uuid4().hex[:8], cmd=cmd, args=args)
        data = c.encode()  # validates whitelist + args
        fut: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._pending[c.id] = fut
        t0 = time.monotonic()
        self.commands_sent.append({"id": c.id, "cmd": cmd, "args": args, "at": datetime.now().astimezone().isoformat()})
        try:
            await self.transport.write_line(data)
            reply = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise HardwareError("timeout", f"No reply to '{cmd}' within {timeout}s") from None
        except TransportClosed as e:
            self._mark_lost()
            raise HardwareError("disconnected", str(e)) from e
        finally:
            self._pending.pop(c.id, None)
        lat = (time.monotonic() - t0) * 1000
        self.latencies_ms.append(lat)
        if self.on_latency and cmd != "heartbeat":
            self.on_latency(lat)
        if reply.ok is False or reply.type == "error":
            raise HardwareError(str(reply.data.get("code", "device_error")), str(reply.data.get("message", "device error")))
        return reply

    def _require_auth(self, cmd: str) -> None:
        if cmd in ACTUATING and not self._authorized:
            raise HardwareError("not_authorized", "No approved, validated design authorizes actuation.")

    async def read_sensor(self) -> dict:
        return (await self._request("read_sensor", {})).data

    async def set_led(self, pin: int, on: bool) -> dict:
        self._require_auth("set_led")
        assert self._authorized
        if pin != self._authorized["led_pin"]:
            raise HardwareError("pin_not_authorized", f"Pin {pin} is not the approved LED pin {self._authorized['led_pin']}.")
        return (await self._request("set_led", {"pin": pin, "on": on})).data

    async def run_test(self, threshold: int, dark_means: str, duration_ms: int) -> dict:
        self._require_auth("run_test")
        return (await self._request("run_test", {"threshold": threshold, "dark_means": dark_means, "duration_ms": duration_ms})).data

    async def stop(self) -> dict:
        """Emergency stop: always allowed, revokes authorization."""
        self.revoke()
        return (await self._request("stop", {})).data

    async def get_capabilities(self) -> dict:
        return (await self._request("get_capabilities", {})).data

    # ---- background ---------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _publish(self, event: dict) -> None:
        for q in self._subscribers:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(event)

    def _mark_lost(self) -> None:
        if self.status == "connected":
            self.status = "lost"
            self._publish({"type": "hardware_status", "status": "lost"})

    async def _reader(self) -> None:
        while True:
            try:
                line = await self.transport.read_line()
            except TransportClosed:
                self._mark_lost()
                return
            try:
                reply = decode(line)
            except ProtocolError:
                continue  # boot noise / garbage is ignored, never executed
            if reply.type == "telemetry":
                d = reply.data
                rec = TelemetryRecord(source="simulated" if self.simulated else "hardware", adc=d.get("adc"), led_state=d.get("led"), raw=d)
                self.telemetry.append(rec)
                self._publish({"type": "telemetry", **rec.model_dump(mode="json")})
            elif reply.id and reply.id in self._pending:
                fut = self._pending[reply.id]
                if not fut.done():
                    fut.set_result(reply)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                await self._request("heartbeat", {}, timeout=self.heartbeat_s)
                self._misses = 0
                self.last_heartbeat = datetime.now().astimezone()
                if self.status == "lost":
                    self.status = "connected"
            except HardwareError:
                self._misses += 1
                if self._misses >= HEARTBEAT_MISS_LIMIT:
                    self._mark_lost()

    def snapshot(self) -> dict:
        last = self.telemetry[-1] if self.telemetry else None
        return {
            "status": self.status,
            "simulated": self.simulated,
            "info": self.info,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "latest": last.model_dump(mode="json") if last else None,
            "authorized_design": self.authorized_design,
            "avg_latency_ms": (sum(self.latencies_ms) / len(self.latencies_ms)) if self.latencies_ms else None,
        }

    async def first_validation(self, expected_board: str = "esp32-devkitc-v4", telemetry_wait_s: float = 2.5) -> dict:
        """Non-actuating readiness checks for the first physical session. Never sends set_led/run_test.

        Every check is pass | fail | unknown | pending with its evidence. A simulated device is flagged and can never
        count as hardware validation.
        """
        checks: list[dict] = []

        def add(cid: str, label: str, status: str, detail: str) -> None:
            checks.append({"id": cid, "label": label, "status": status, "detail": detail})

        if self.status != "connected":
            add("connected", "Session connected", "fail", f"status is '{self.status}'")
            return {"simulated": self.simulated, "checks": checks, "ok": False, "actuated": False}
        fw = str(self.info.get("fw", ""))
        add("handshake", "Firmware handshake", "pass" if fw.startswith("buildloop") else "fail", f"fw='{fw}' version='{self.info.get('fw_version', '?')}'")
        add("protocol", "Protocol version 1", "pass", "replies decoded as v1 (other versions are rejected by the decoder)")
        board = str(self.info.get("board", ""))
        add("board", "Board profile matches the design", "pass" if board == expected_board else "fail", f"device reports '{board}', design needs '{expected_board}'")
        try:
            t0 = time.monotonic()
            await self._request("heartbeat", {}, timeout=2.0)
            add("heartbeat", "Heartbeat reply", "pass", f"{(time.monotonic() - t0) * 1000:.1f} ms round trip")
        except HardwareError as e:
            add("heartbeat", "Heartbeat reply", "fail", f"{e.code}: {e.message}")
        n0 = len(self.telemetry)
        deadline = time.monotonic() + telemetry_wait_s
        while time.monotonic() < deadline and len(self.telemetry) - n0 < 2:
            await asyncio.sleep(0.1)
        got = len(self.telemetry) - n0
        add("telemetry", "Telemetry stream", "pass" if got >= 2 else "fail", f"{got} frames in {telemetry_wait_s}s" + ("" if got >= 2 else " (firmware should emit ~5/s)"))
        actuated = any(c["cmd"] in ACTUATING for c in self.commands_sent)
        last = self.telemetry[-1] if self.telemetry else None
        if actuated:
            add("safe_defaults", "LED off before any command", "unknown", "an actuating command was already sent this session; reconnect to test boot defaults")
        elif last is None or last.led_state is None:
            add("safe_defaults", "LED off before any command", "unknown", "no telemetry with LED state")
        else:
            add("safe_defaults", "LED off before any command", "pass" if last.led_state is False else "fail", f"LED pin reported {'ON' if last.led_state else 'OFF'} with no command sent")
        add("led_circuit", "Current-limited LED test", "pending", "needs your wiring confirmation and execution authorization; not run here")
        add("optical", "Independent optical evidence", "pending", "camera region check after the LED test")
        ok = all(c["status"] == "pass" for c in checks if c["status"] != "pending")
        return {"simulated": self.simulated, "checks": checks, "ok": ok, "actuated": actuated}

