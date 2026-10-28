"""HTTP-level closed-loop test (Demo Mode, simulated device). Run: pytest tests/integration -c apps/api/pyproject.toml"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from buildloop.config import Settings  # noqa: E402
from buildloop.main import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'i.db'}", MODEL_API_KEY=""))
    with TestClient(app) as c:
        yield c


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_closed_loop_over_http(client):
    c = client
    assert ok(c.get("/api/health"))["meta_configured"] is False
    pid = ok(c.post("/api/projects", json={"name": "it", "mode": "demo"}))["id"]
    frame = c.get("/api/perception/demo-frame.jpg").content
    scan = ok(c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", frame, "image/jpeg")}))
    assert scan["live"] is False and len(scan["components"]) == 6

    # confirm inventory
    for comp in ok(c.get(f"/api/inventory/{pid}")):
        patch = {"confirm": True}
        if comp["type"] == "resistor":
            patch["properties"] = {"resistance_ohm": 220 if comp["name"].endswith("A") else 10000}
        if comp["type"] == "led":
            patch["properties"] = {"forward_v": 2.0}
        ok(c.patch(f"/api/inventory/{pid}/{comp['id']}", json=patch))
    ok(c.post(f"/api/projects/{pid}/stage", json={"stage": "PLAN"}))
    designs = ok(c.post(f"/api/designs/{pid}/plan", json={}))
    ldr = next(d for d in designs if d["behavior"]["sensor_source"] == "photoresistor_adc")
    steps = ok(c.post(f"/api/designs/{pid}/{ldr['id']}/approve"))
    for s in steps:
        ok(c.post(f"/api/assembly/{pid}/{ldr['id']}/{s['index']}/confirm"))

    # authorization requires hardware; simulated allowed in demo mode only
    assert c.post(f"/api/hardware/{pid}/authorize").status_code == 409
    ok(c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid}))
    ok(c.post(f"/api/hardware/{pid}/authorize"))
    ok(c.post(f"/api/verification/{pid}/photoresistor/start"))
    ok(c.post("/api/hardware/sim/ambient", json={"value": 0.9}))
    ok(c.post(f"/api/verification/{pid}/phase", json={"label": "bright", "expected_led": False, "seconds": 0.8}))
    ok(c.post("/api/hardware/sim/ambient", json={"value": 0.1}))
    ok(c.post(f"/api/verification/{pid}/phase", json={"label": "dark", "expected_led": True, "seconds": 0.8}))
    v = ok(c.post(f"/api/verification/{pid}/photoresistor/finish"))
    assert v["passed"] is True and v["level"] == "sensor_response_observed"

    # remove photoresistor -> invalidation + redesign
    ldr_comp = next(x for x in ok(c.get(f"/api/inventory/{pid}")) if x["type"] == "photoresistor")
    ok(c.patch(f"/api/inventory/{pid}/{ldr_comp['id']}", json={"available": False}))
    view = ok(c.get(f"/api/projects/{pid}"))
    assert view["project"]["stage"] == "REDESIGN" and ldr["id"] in view["state"]["invalidated"]
    assert view["hardware"]["authorized_design"] is None
    alts = ok(c.post(f"/api/designs/{pid}/plan", json={}))
    phone = next(d for d in alts if d["behavior"]["sensor_source"] == "phone_camera_brightness")
    for s in ok(c.post(f"/api/designs/{pid}/{phone['id']}/approve")):
        ok(c.post(f"/api/assembly/{pid}/{phone['id']}/{s['index']}/confirm"))
    ok(c.post(f"/api/hardware/{pid}/authorize"))
    for b in (0.9, 0.1, 0.9):
        ok(c.post(f"/api/verification/{pid}/phone/brightness", json={"value": b}))
    v2 = ok(c.post(f"/api/verification/{pid}/phone/finish"))
    assert v2["passed"] is True
    cmp = ok(c.get(f"/api/compare/{pid}"))
    assert cmp["original"]["id"] == ldr["id"] and cmp["revised"]["id"] == phone["id"]

    # emergency stop
    ok(c.post(f"/api/hardware/{pid}/stop"))
    assert ok(c.get("/api/hardware/status"))["authorized_design"] is None


def test_simulated_hardware_refused_in_real_mode(client):
    pid = ok(client.post("/api/projects", json={"name": "r", "mode": "real"}))["id"]
    r = client.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    assert r.status_code == 400


def test_real_mode_scan_without_key_reports_clear_error(client):
    pid = ok(client.post("/api/projects", json={"name": "r", "mode": "real"}))["id"]
    r = client.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    assert r.status_code == 409 and r.json()["code"] == "no_api_key"


def test_unknown_project_404(client):
    r = client.get("/api/projects/nope")
    assert r.status_code == 404 and r.json()["code"] == "project_not_found"


def test_phone_websocket_converts_brightness_to_led(client):
    c = client
    pid = ok(c.post("/api/projects", json={"name": "w", "mode": "demo"}))["id"]
    ok(c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", c.get("/api/perception/demo-frame.jpg").content, "image/jpeg")}))
    for comp in ok(c.get(f"/api/inventory/{pid}")):
        patch = {"confirm": True}
        if comp["type"] == "resistor":
            patch["properties"] = {"resistance_ohm": 220 if comp["name"].endswith("A") else 10000}
        if comp["type"] == "led":
            patch["properties"] = {"forward_v": 2.0}
        ok(c.patch(f"/api/inventory/{pid}/{comp['id']}", json=patch))
    ldr = next(x for x in ok(c.get(f"/api/inventory/{pid}")) if x["type"] == "photoresistor")
    ok(c.patch(f"/api/inventory/{pid}/{ldr['id']}", json={"available": False}))
    ok(c.post(f"/api/projects/{pid}/stage", json={"stage": "PLAN"}) if False else c.get("/api/health"))
    designs = ok(c.post(f"/api/designs/{pid}/plan", json={}))
    phone = next(d for d in designs if d["behavior"]["sensor_source"] == "phone_camera_brightness")
    for s in ok(c.post(f"/api/designs/{pid}/{phone['id']}/approve")):
        ok(c.post(f"/api/assembly/{pid}/{phone['id']}/{s['index']}/confirm"))
    ok(c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid}))
    ok(c.post(f"/api/hardware/{pid}/authorize"))
    with c.websocket_connect(f"/ws/phone/{pid}") as ws:
        ws.send_json({"brightness": 0.1})
        assert ws.receive_json()["led"] is True
        ws.send_json({"brightness": 0.9})
        assert ws.receive_json()["led"] is False
        ws.send_json({"nonsense": 1})
        assert ws.receive_json()["type"] == "error"
    time.sleep(0)
