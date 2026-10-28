"""Closed-loop adaptive-light workflow on simulated hardware (Demo Mode). Nothing here is physical evidence."""
import asyncio

import pytest

from buildloop.config import Settings
from buildloop.db import Store
from buildloop.hardware.session import HardwareSession
from buildloop.hardware.transport import FakeDeviceTransport
from buildloop.models import ComponentType, Mode, Stage, VerificationLevel, VStatus
from buildloop.providers import make_provider_factory
from buildloop.workflow import Engine, WorkflowError


@pytest.fixture
def engine(tmp_path):
    s = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'e.db'}", MODEL_API_KEY="")
    return Engine(Store(s.DATABASE_URL), make_provider_factory(s))


async def confirm_inventory(e: Engine, pid: str):
    for c in e.components(pid):
        patch = {"confirm": True}
        if c.type == ComponentType.RESISTOR:
            patch["properties"] = {"resistance_ohm": 220 if c.name.endswith("A") else 10000}
        if c.type == ComponentType.LED:
            patch["properties"] = {"forward_v": 2.0}
        e.update_component(pid, c.id, patch)


async def build_and_authorize(e: Engine, pid: str, design):
    e.approve_design(pid, design.id)
    for s in e.state(pid).steps_by_design[design.id]:
        e.confirm_step(pid, design.id, s.index)
    return e.authorize_execution(pid)


async def test_full_closed_loop(engine):
    e = engine
    p = e.create_project("t", "Build a light that turns on when dark", Mode.DEMO, "esp32-devkitc-v4")
    r = await e.scan(p.id, b"synthetic")
    assert len(r["components"]) == 6 and r["live"] is False
    assert e.project(p.id).stage == Stage.CONFIRM

    # unknown resistances are not silently assumed: design cannot be approved
    designs = await e.plan(p.id)
    ldr = next(d for d in designs if d.behavior.sensor_source == "photoresistor_adc")
    assert any(v.status == VStatus.UNKNOWN for v in ldr.validation)
    with pytest.raises(WorkflowError) as ex:
        e.approve_design(p.id, ldr.id)
    assert ex.value.code == "validation_blocked"

    # user confirms -> re-plan passes
    await confirm_inventory(e, p.id)
    e.set_stage(p.id, Stage.PLAN, force=True)
    designs = await e.plan(p.id)
    ldr = next(d for d in designs if d.behavior.sensor_source == "photoresistor_adc")
    assert ldr.overall != VStatus.BLOCKED and VStatus.UNKNOWN not in {v.status for v in ldr.validation}

    # hardware gates
    with pytest.raises(WorkflowError):
        e.set_stage(p.id, Stage.TEST, force=True)  # no approved design/steps yet
    e.approve_design(p.id, ldr.id)
    with pytest.raises(WorkflowError) as ex:
        e.authorize_execution(p.id)
    assert ex.value.code == "assembly_unconfirmed"
    for s in e.state(p.id).steps_by_design[ldr.id]:
        e.confirm_step(p.id, ldr.id, s.index)
    with pytest.raises(WorkflowError) as ex:
        e.authorize_execution(p.id)
    assert ex.value.code == "hardware_unavailable"

    sess = HardwareSession(FakeDeviceTransport(telemetry_hz=20, ambient=0.9), heartbeat_s=5)
    await e.attach_hardware(sess)
    e.authorize_execution(p.id)
    assert e.project(p.id).stage == Stage.TEST
    await e.start_photoresistor_test(p.id)
    sess.transport.ambient = 0.9
    await e.capture_phase(p.id, "bright", False, 0.5)
    sess.transport.ambient = 0.1
    await e.capture_phase(p.id, "dark", True, 0.5)
    v1 = await e.finish_photoresistor_test(p.id)
    assert v1.passed is True
    assert v1.level == VerificationLevel.SENSOR_OBSERVED  # simulated can never exceed this
    assert "SIMULATED" in v1.summary
    assert e.project(p.id).stage == Stage.COMPARE

    # reality changes: photoresistor removed
    ldr_comp = next(c for c in e.components(p.id) if c.type == ComponentType.PHOTORESISTOR)
    e.update_component(p.id, ldr_comp.id, {"available": False})
    assert e.project(p.id).stage == Stage.REDESIGN
    assert ldr.id in e.state(p.id).invalidated
    assert sess.authorized_design is None  # execution authorization revoked
    assert not e.state(p.id).approved_for_execution
    assert any(r.kind == "design_invalidated" for r in e.revisions(p.id))

    # redesign with phone camera substitute
    alts = await e.plan(p.id)
    assert all(a.behavior.sensor_source == "phone_camera_brightness" or a.overall == VStatus.BLOCKED for a in alts)
    phone = next(a for a in alts if a.behavior.sensor_source == "phone_camera_brightness")
    assert phone.overall != VStatus.BLOCKED
    assert any("substitute" in n.lower() for n in phone.substitute_notes)
    await build_and_authorize(e, p.id, phone)
    for b in (0.8, 0.8, 0.1, 0.1, 0.8):
        await e.phone_brightness(p.id, b)
    v2 = await e.finish_phone_test(p.id)
    assert v2.passed is True and v2.level == VerificationLevel.SENSOR_OBSERVED
    assert "not physical evidence" in v2.summary

    cmp = e.comparison(p.id)
    assert cmp["original"]["id"] == ldr.id and cmp["revised"]["id"] == phone.id
    assert "Photoresistor" in cmp["removed_components"]
    assert cmp["original"]["invalidated_reasons"]

    # restart: history preserved
    e2 = Engine(Store(e.store.engine.url.render_as_string(hide_password=False)), e._providers)
    assert len(e2.revisions(p.id)) == len(e.revisions(p.id))
    assert e2.state(p.id).original_design_id == ldr.id
    await sess.disconnect()


async def test_real_mode_never_uses_demo_providers(engine):
    p = engine.create_project("r", "g", Mode.REAL, "esp32-devkitc-v4")
    with pytest.raises(WorkflowError) as ex:
        await engine.scan(p.id, b"\xff\xd8not-a-real-jpeg")
    assert ex.value.code == "no_api_key"


async def test_illegal_stage_transition_rejected(engine):
    p = engine.create_project("t", "g", Mode.DEMO, "esp32-devkitc-v4")
    with pytest.raises(WorkflowError) as ex:
        engine.set_stage(p.id, Stage.TEST)
    assert ex.value.code == "illegal_transition"


async def test_emergency_stop_revokes(engine):
    p = engine.create_project("t", "g", Mode.DEMO, "esp32-devkitc-v4")
    sess = HardwareSession(FakeDeviceTransport(), heartbeat_s=5)
    await engine.attach_hardware(sess)
    sess.authorize("x", 25)
    await engine.emergency_stop(p.id)
    assert sess.authorized_design is None
    await sess.disconnect()


async def test_user_rejects_design(engine):
    p = engine.create_project("t", "g", Mode.DEMO, "esp32-devkitc-v4")
    await engine.scan(p.id, b"x")
    await confirm_inventory(engine, p.id)
    d = (await engine.plan(p.id))[0]
    engine.reject_design(p.id, d.id, "no thanks")
    assert any(r.kind == "decision" and "rejected" in r.summary for r in engine.revisions(p.id))
    assert engine.state(p.id).active_design_id is None
    _ = asyncio  # keep import for pytest-asyncio loops
