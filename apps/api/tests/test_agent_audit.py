"""Audit of the agent control loop: real state transitions, rejected actions, validation failures, replanning, authorization.

Demo Mode with a simulated device: nothing here is physical or live-Meta evidence.
"""
import pytest
from fastapi.testclient import TestClient

from buildloop.config import Settings
from buildloop.main import create_app


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'a.db'}", MODEL_API_KEY=""))) as client:
        yield client


def new_project(c):
    pid = c.post("/api/projects", json={"name": "audit", "mode": "demo"}).json()["id"]
    c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    return pid


def confirm(c, pid, resistor_a=220.0):
    for comp in c.get(f"/api/inventory/{pid}").json():
        patch = {"confirm": True}
        if comp["type"] == "resistor":
            patch["properties"] = {"resistance_ohm": resistor_a if comp["name"].endswith("A") else 10000}
        if comp["type"] == "led":
            patch["properties"] = {"forward_v": 2.0}
        c.patch(f"/api/inventory/{pid}/{comp['id']}", json=patch)


def stages(c, pid):
    """Stage sequence as recorded in the revision log (the audit trail), oldest first."""
    return [r["summary"] for r in c.get(f"/api/revisions/{pid}").json() if r["kind"] == "stage"]


def build(c, pid, design):
    steps = c.post(f"/api/designs/{pid}/{design['id']}/approve").json()
    for s in steps:
        c.post(f"/api/assembly/{pid}/{design['id']}/{s['index']}/confirm")


def test_recorded_stage_path_is_exactly_the_state_machine(c):
    pid = new_project(c)
    confirm(c, pid)
    ldr = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "photoresistor_adc")
    build(c, pid, ldr)
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    c.post(f"/api/hardware/{pid}/authorize")
    c.post(f"/api/verification/{pid}/photoresistor/start")
    c.post("/api/hardware/sim/ambient", json={"value": 0.9})
    c.post(f"/api/verification/{pid}/phase", json={"label": "bright", "expected_led": False, "seconds": 0.6})
    c.post("/api/hardware/sim/ambient", json={"value": 0.1})
    c.post(f"/api/verification/{pid}/phase", json={"label": "dark", "expected_led": True, "seconds": 0.6})
    assert c.post(f"/api/verification/{pid}/photoresistor/finish").json()["passed"] is True
    part = next(x for x in c.get(f"/api/inventory/{pid}").json() if x["type"] == "photoresistor")
    c.patch(f"/api/inventory/{pid}/{part['id']}", json={"available": False})
    phone = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "phone_camera_brightness")
    build(c, pid, phone)
    c.post(f"/api/hardware/{pid}/authorize")
    for b in (0.9, 0.1, 0.9):
        c.post(f"/api/verification/{pid}/phone/brightness", json={"value": b})
    assert c.post(f"/api/verification/{pid}/phone/finish").json()["passed"] is True
    assert stages(c, pid) == [
        "OBSERVE -> INTERPRET", "INTERPRET -> CONFIRM", "CONFIRM -> PLAN", "PLAN -> VALIDATE", "VALIDATE -> GUIDE",
        "GUIDE -> TEST", "TEST -> COMPARE", "COMPARE -> REDESIGN", "REDESIGN -> VALIDATE", "VALIDATE -> GUIDE",
        "GUIDE -> TEST", "TEST -> COMPARE",
    ]


def test_illegal_stage_jump_is_rejected_and_logged_nowhere(c):
    pid = new_project(c)
    before = stages(c, pid)
    r = c.post(f"/api/projects/{pid}/stage", json={"stage": "TEST"})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    assert stages(c, pid) == before


def test_unknown_resistance_blocks_approval_and_agent_names_it(c):
    pid = new_project(c)
    a = c.get(f"/api/agent/{pid}").json()
    assert a["next_action"]["kind"] == "confirm_inventory" and any("Unknown resistance" in b for b in a["blockers"])
    designs = c.post(f"/api/designs/{pid}/plan", json={}).json()
    r = c.post(f"/api/designs/{pid}/{designs[0]['id']}/approve")
    assert r.status_code == 409 and r.json()["code"] == "validation_blocked"


def test_all_candidates_blocked_makes_agent_ask_for_fixes(c):
    pid = new_project(c)
    confirm(c, pid)
    led = next(x for x in c.get(f"/api/inventory/{pid}").json() if x["type"] == "led")
    c.patch(f"/api/inventory/{pid}/{led['id']}", json={"available": False})  # no LED: every design is infeasible
    designs = c.post(f"/api/designs/{pid}/plan", json={}).json()
    assert designs and all(any(v["status"] == "BLOCKED" for v in d["validation"]) for d in designs)
    a = c.get(f"/api/agent/{pid}").json()
    assert a["next_action"]["kind"] == "replan" and "No candidate passes validation" in a["blockers"]
    assert c.post(f"/api/designs/{pid}/{designs[0]['id']}/approve").status_code == 409


def test_authorization_gates_in_order(c):
    pid = new_project(c)
    confirm(c, pid)
    ldr = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "photoresistor_adc")
    # no approved design yet
    assert c.post(f"/api/hardware/{pid}/authorize").json()["code"] == "no_design"
    steps = c.post(f"/api/designs/{pid}/{ldr['id']}/approve").json()
    # steps unconfirmed
    assert c.post(f"/api/hardware/{pid}/authorize").json()["code"] == "assembly_unconfirmed"
    # confirming out of order is refused
    assert c.post(f"/api/assembly/{pid}/{ldr['id']}/3/confirm").json()["code"] == "steps_out_of_order"
    for s in steps:
        c.post(f"/api/assembly/{pid}/{ldr['id']}/{s['index']}/confirm")
    # no board
    assert c.post(f"/api/hardware/{pid}/authorize").json()["code"] == "hardware_unavailable"
    # tests refuse to run without authorization
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    assert c.post(f"/api/verification/{pid}/photoresistor/start").json()["code"] == "not_authorized"
    assert c.post(f"/api/verification/{pid}/phone/brightness", json={"value": 0.1}).json()["code"] == "not_authorized"
    assert c.post(f"/api/verification/{pid}/optical/command", json={"on": True, "phase": "on"}).json()["code"] == "not_authorized"
    assert c.post(f"/api/hardware/{pid}/authorize").status_code == 200


def test_emergency_stop_revokes_and_agent_asks_to_reauthorize(c):
    pid = new_project(c)
    confirm(c, pid)
    ldr = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "photoresistor_adc")
    build(c, pid, ldr)
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    c.post(f"/api/hardware/{pid}/authorize")
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "run_test"
    c.post(f"/api/hardware/{pid}/stop")
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "authorize"
    assert c.post(f"/api/verification/{pid}/photoresistor/start").json()["code"] == "not_authorized"


def test_phone_command_to_wrong_pin_cannot_be_sent(c):
    """The model/tool surface can only drive the approved LED pin: a different pin is refused by the session."""
    import asyncio

    from buildloop.hardware.session import HardwareError, HardwareSession
    from buildloop.hardware.transport import FakeDeviceTransport

    async def go():
        s = HardwareSession(FakeDeviceTransport(), heartbeat_s=5)
        await s.connect()
        s.authorize("d", 25)
        with pytest.raises(HardwareError) as e:
            await s.set_led(2, True)
        await s.disconnect()
        return e.value.code

    assert asyncio.run(go()) == "pin_not_authorized"


def test_detections_stay_distinct_from_user_confirmed_identities(c):
    pid = new_project(c)
    comps = c.get(f"/api/inventory/{pid}").json()
    assert comps and all(x["status"] in ("detected", "uncertain") for x in comps)  # model predictions only
    c.patch(f"/api/inventory/{pid}/{comps[0]['id']}", json={"confirm": True, "type": "unknown"})
    after = {x["id"]: x for x in c.get(f"/api/inventory/{pid}").json()}
    assert after[comps[0]["id"]]["status"] == "user_confirmed" and after[comps[0]["id"]]["type"] == "unknown"
    assert all(x["status"] != "user_confirmed" for k, x in after.items() if k != comps[0]["id"])
    assert any(m["name"] == "classification.correction" for m in c.get("/api/metrics/raw").json())


def test_first_validation_is_non_actuating_and_flags_simulation(c):
    pid = new_project(c)
    assert c.post("/api/hardware/validate").status_code == 409  # no session
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    v = c.post("/api/hardware/validate").json()
    by = {x["id"]: x for x in v["checks"]}
    assert v["simulated"] is True and v["actuated"] is False
    assert [by[k]["status"] for k in ("handshake", "protocol", "board", "heartbeat", "telemetry", "safe_defaults")] == ["pass"] * 6
    assert by["led_circuit"]["status"] == "pending" and by["optical"]["status"] == "pending"
    assert c.get("/api/hardware/status").json()["authorized_design"] is None  # nothing authorized or driven


def test_first_validation_fails_when_led_is_on_at_boot():
    import asyncio

    from buildloop.hardware.session import HardwareSession
    from buildloop.hardware.transport import FakeDeviceTransport

    async def go():
        t = FakeDeviceTransport(telemetry_hz=20)
        t.led = True  # firmware that boots with the LED pin high
        s = HardwareSession(t, heartbeat_s=5)
        await s.connect()
        r = await s.first_validation(telemetry_wait_s=1.0)
        await s.disconnect()
        return r

    r = asyncio.run(go())
    safe = next(x for x in r["checks"] if x["id"] == "safe_defaults")
    assert safe["status"] == "fail" and r["ok"] is False


def test_manual_removal_is_labelled_as_override_not_camera_observation(c):
    pid = new_project(c)
    confirm(c, pid)
    ldr = next(x for x in c.get(f"/api/inventory/{pid}").json() if x["type"] == "photoresistor")
    assert ldr["bbox"] is not None  # the scanned frame contains it
    design = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "photoresistor_adc")
    build(c, pid, design)
    c.patch(f"/api/inventory/{pid}/{ldr['id']}", json={"available": False})
    ev = next(r for r in c.get(f"/api/revisions/{pid}").json() if r["kind"] == "inventory_change" and r["data"].get("removed"))
    assert "manual override" in ev["summary"] and "still shows it" in ev["summary"] and ev["data"]["manual_override"] is True
    trig = c.get(f"/api/agent/{pid}").json()["replan_triggers"]
    assert trig and "manual override" in trig[0] and "not camera-observed" in trig[0]
