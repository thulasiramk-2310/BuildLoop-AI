"""Versioned newline-delimited JSON protocol. The command set is a closed whitelist."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

PROTOCOL_VERSION = 1
MAX_LINE = 512


class ProtocolError(Exception):
    pass


class CmdHello(BaseModel):
    pass


class CmdHeartbeat(BaseModel):
    pass


class CmdGetCapabilities(BaseModel):
    pass


class CmdReadSensor(BaseModel):
    pass


class CmdSetLed(BaseModel):
    pin: int = Field(ge=0, le=48)
    on: bool


class CmdRunTest(BaseModel):
    threshold: int = Field(ge=0, le=4095)
    dark_means: Literal["adc_low", "adc_high"]
    duration_ms: int = Field(ge=500, le=60000)


class CmdStop(BaseModel):
    pass


COMMANDS: dict[str, type[BaseModel]] = {
    "hello": CmdHello,
    "heartbeat": CmdHeartbeat,
    "get_capabilities": CmdGetCapabilities,
    "read_sensor": CmdReadSensor,
    "set_led": CmdSetLed,
    "run_test": CmdRunTest,
    "stop": CmdStop,
}
# Commands that change physical outputs: require explicit authorization in HardwareSession.
ACTUATING = {"set_led", "run_test"}


class Command(BaseModel):
    v: int = PROTOCOL_VERSION
    id: str
    cmd: str
    args: dict[str, Any] = {}

    @field_validator("cmd")
    @classmethod
    def _whitelisted(cls, v: str) -> str:
        if v not in COMMANDS:
            raise ValueError(f"command '{v}' is not whitelisted")
        return v

    def validated_args(self) -> BaseModel:
        return COMMANDS[self.cmd].model_validate(self.args)

    def encode(self) -> bytes:
        self.validated_args()
        line = json.dumps(self.model_dump(), separators=(",", ":"))
        if len(line) > MAX_LINE:
            raise ProtocolError("command too long")
        return (line + "\n").encode()


class Reply(BaseModel):
    v: int
    type: Literal["hello", "ack", "error", "telemetry", "capabilities", "heartbeat", "test_result"]
    id: str | None = None
    ok: bool | None = None
    data: dict[str, Any] = {}

    model_config = {"extra": "allow"}


def decode(line: bytes | str) -> Reply:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        raise ProtocolError("empty line")
    if len(line) > MAX_LINE * 4:
        raise ProtocolError("line too long")
    try:
        obj = json.loads(line)
        reply = Reply.model_validate(obj)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ProtocolError(f"invalid message: {e}") from e
    if reply.v != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {reply.v}")
    return reply
