import asyncio

import pytest

from buildloop.hardware.protocol import Command, ProtocolError, decode
from buildloop.hardware.session import HardwareError, HardwareSession
from buildloop.hardware.transport import FakeDeviceTransport


def test_command_whitelist_rejects_unknown():
    with pytest.raises(ValueError):
        Command(id="1", cmd="exec", args={"code": "import os"})


def test_command_args_validated():
    with pytest.raises(ValueError):
        Command(id="1", cmd="set_led", args={"pin": 999, "on": True}).encode()


def test_decode_rejects_garbage_and_wrong_version():
    with pytest.raises(ProtocolError):
        decode(b"E (123) boot: rst\n")
    with pytest.raises(ProtocolError):
        decode('{"v":2,"type":"ack"}')


async def make(**kw):
    s = HardwareSession(FakeDeviceTransport(**kw), heartbeat_s=0.2)
    await s.connect()
    return s


async def test_handshake_and_simulated_flag():
    s = await make()
    assert s.status == "connected" and s.simulated and s.info["led_pin"] == 25
    await s.disconnect()


async def test_actuation_requires_authorization():
    s = await make()
    with pytest.raises(HardwareError) as e:
        await s.set_led(25, True)
    assert e.value.code == "not_authorized"
    s.authorize("dsg_1", 25)
    assert (await s.set_led(25, True))["led"] is True
    with pytest.raises(HardwareError) as e:
        await s.set_led(26, True)
    assert e.value.code == "pin_not_authorized"
    await s.disconnect()


async def test_stop_revokes_authorization():
    s = await make()
    s.authorize("d", 25)
    await s.set_led(25, True)
    await s.stop()
    assert s.authorized_design is None
    assert s.transport.led is False
    await s.disconnect()


async def test_command_timeout():
    s = await make()
    s.transport.mute = True
    s.authorize("d", 25)
    with pytest.raises(HardwareError) as e:
        await s._request("read_sensor", {}, timeout=0.2)
    assert e.value.code == "timeout"
    await s.disconnect()


async def test_disconnect_during_telemetry_marks_lost():
    s = await make()
    await asyncio.sleep(0.3)
    assert s.telemetry
    s.transport.disconnect()
    await asyncio.sleep(0.2)
    assert s.status == "lost"
    await s.disconnect()


async def test_telemetry_is_labelled_simulated():
    s = await make()
    await asyncio.sleep(0.5)
    assert all(t.source == "simulated" for t in s.telemetry)
    await s.disconnect()


async def test_unsupported_firmware_rejected():
    class Odd(FakeDeviceTransport):
        async def write_line(self, data):
            import json

            cid = json.loads(data)["id"]
            self._emit({"v": 1, "id": cid, "type": "hello", "ok": True, "data": {"fw": "other"}})

    s = HardwareSession(Odd())
    with pytest.raises(HardwareError) as e:
        await s.connect()
    assert e.value.code == "unsupported_device" and s.status == "unsupported"


async def test_run_test_autonomous_led_follows_light():
    s = await make()
    s.authorize("d", 25)
    await s.run_test(1500, "adc_low", 5000)
    s.transport.ambient = 0.1
    await asyncio.sleep(0.5)
    assert s.telemetry[-1].led_state is True
    s.transport.ambient = 0.9
    await asyncio.sleep(0.5)
    assert s.telemetry[-1].led_state is False
    await s.disconnect()
