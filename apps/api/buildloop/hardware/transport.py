"""Byte transports: real serial and an explicitly simulated device."""
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import serial
import serial.tools.list_ports


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str


def list_ports() -> list[PortInfo]:
    return [PortInfo(p.device, p.description, p.hwid) for p in serial.tools.list_ports.comports()]


class TransportClosed(Exception):
    pass


class Transport(ABC):
    simulated: bool = False

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def write_line(self, data: bytes) -> None: ...

    @abstractmethod
    async def read_line(self) -> bytes:
        """Block until a full line arrives. Raise TransportClosed on disconnect."""


class SerialTransport(Transport):
    def __init__(self, port: str, baud: int = 115200):
        self.port, self.baud = port, baud
        self._ser: serial.Serial | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self._ser = await loop.run_in_executor(None, lambda: serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=1.0))
        # Opening can reset ESP32 boards via DTR/RTS; give boot time before the handshake.
        await asyncio.sleep(1.5)
        self._ser.reset_input_buffer()

    async def close(self) -> None:
        if self._ser:
            ser, self._ser = self._ser, None
            await asyncio.get_running_loop().run_in_executor(None, ser.close)

    async def write_line(self, data: bytes) -> None:
        if not self._ser:
            raise TransportClosed("serial closed")
        ser = self._ser
        try:
            await asyncio.get_running_loop().run_in_executor(None, ser.write, data)
        except (serial.SerialException, OSError) as e:
            raise TransportClosed(str(e)) from e

    async def read_line(self) -> bytes:
        loop = asyncio.get_running_loop()
        while True:
            ser = self._ser
            if ser is None:
                raise TransportClosed("serial closed")
            try:
                line = await loop.run_in_executor(None, ser.readline)
            except (serial.SerialException, OSError) as e:
                raise TransportClosed(str(e)) from e
            if line.endswith(b"\n"):
                return line
            # partial/timeout: keep waiting (boot logs without newline are discarded on next newline)


class FakeDeviceTransport(Transport):
    """Simulated BuildLoop firmware. Everything it reports is flagged simulated by HardwareSession."""

    simulated = True

    def __init__(self, *, led_pin: int = 25, adc_pin: int = 34, ambient: float = 0.8, telemetry_hz: float = 5.0):
        self.led_pin, self.adc_pin = led_pin, adc_pin
        self.ambient = ambient  # 0 dark .. 1 bright; LDR divider: brighter -> higher ADC
        self.telemetry_period = 1.0 / telemetry_hz
        self.led = False
        self.auto: dict | None = None
        self.mute = False  # drop replies (timeout tests)
        self.dropped = False  # simulate disconnect
        self._out: asyncio.Queue[bytes] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._t0 = time.monotonic()

    @property
    def adc(self) -> int:
        return int(max(0.0, min(1.0, self.ambient)) * 4095)

    async def open(self) -> None:
        self._task = asyncio.create_task(self._telemetry_loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        self.dropped = True
        self._out.put_nowait(b"")

    def disconnect(self) -> None:
        self.dropped = True
        self._out.put_nowait(b"")

    def _emit(self, obj: dict) -> None:
        if not self.mute:
            self._out.put_nowait((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def _telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(self.telemetry_period)
            if self.auto:
                dark = self.adc < self.auto["threshold"] if self.auto["dark_means"] == "adc_low" else self.adc > self.auto["threshold"]
                self.led = dark
                if (time.monotonic() - self.auto["t0"]) * 1000 > self.auto["duration_ms"]:
                    self.auto = None
                    self.led = False
                    self._emit({"v": 1, "type": "test_result", "data": {"finished": True}})
            self._emit({"v": 1, "type": "telemetry", "data": {"t": int((time.monotonic() - self._t0) * 1000), "adc": self.adc, "led": self.led}})

    async def write_line(self, data: bytes) -> None:
        if self.dropped:
            raise TransportClosed("device disconnected")
        msg = json.loads(data)
        cid, cmd, args = msg["id"], msg["cmd"], msg.get("args", {})
        base = {"v": 1, "id": cid}
        if cmd == "hello":
            self._emit({**base, "type": "hello", "ok": True, "data": {"fw": "buildloop-sim", "fw_version": "0.1.0", "board": "esp32-devkitc-v4", "led_pin": self.led_pin, "adc_pin": self.adc_pin, "simulated": True}})
        elif cmd == "heartbeat":
            self._emit({**base, "type": "heartbeat", "ok": True})
        elif cmd == "get_capabilities":
            self._emit({**base, "type": "capabilities", "ok": True, "data": {"commands": ["hello", "heartbeat", "get_capabilities", "read_sensor", "set_led", "run_test", "stop"], "led_pin": self.led_pin, "adc_pin": self.adc_pin}})
        elif cmd == "read_sensor":
            self._emit({**base, "type": "ack", "ok": True, "data": {"adc": self.adc, "led": self.led}})
        elif cmd == "set_led":
            if args["pin"] != self.led_pin:
                self._emit({**base, "type": "error", "ok": False, "data": {"code": "pin_not_allowed", "message": f"pin {args['pin']} is not the configured LED pin"}})
            else:
                self.auto = None
                self.led = bool(args["on"])
                self._emit({**base, "type": "ack", "ok": True, "data": {"led": self.led}})
        elif cmd == "run_test":
            self.auto = {**args, "t0": time.monotonic()}
            self._emit({**base, "type": "ack", "ok": True, "data": {"running": True}})
        elif cmd == "stop":
            self.auto, self.led = None, False
            self._emit({**base, "type": "ack", "ok": True, "data": {"led": False}})

    async def read_line(self) -> bytes:
        item = await self._out.get()
        if item == b"":
            raise TransportClosed("device disconnected")
        return item
