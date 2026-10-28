"""Phase 2: Meta request shapes (mocked transport, NOT live), optical verification, metrics, diagnostics, approval gates."""
import json

import httpx
import pytest
from conftest import comp
from fastapi.testclient import TestClient

from buildloop.config import Settings
from buildloop.designs import build_phone_sensor_light
from buildloop.main import create_app
from buildloop.metrics import Metrics
from buildloop.models import ComponentType, VerificationLevel
from buildloop.optical import OpticalSample, evaluate_optical
from buildloop.providers.base import PlanContext, ProviderError
from buildloop.providers.meta import MetaClient, MetaReasoning
from buildloop.verification import apply_optical


def samples(off1, on, off2, noise=0.0, n=8):
    out = []
    for phase, base in (("off_before", off1), ("on", on), ("off_after", off2)):
        for i in range(n):
            out.append(OpticalSample(t=float(i), luma=base + (noise if i % 2 else -noise), phase=phase))
    return out


# ---- optical ---------------------------------------------------------------------------------
def test_optical_observed():
    r = evaluate_optical(samples(0.20, 0.45, 0.20, noise=0.005))
    assert r.outcome == "observed" and r.delta > 0.2


def test_optical_not_observed_when_no_change():
    r = evaluate_optical(samples(0.20, 0.21, 0.20, noise=0.002))
    assert r.outcome == "not_observed"


def test_optical_uncertain_on_drift():
    r = evaluate_optical(samples(0.20, 0.40, 0.30))
    assert r.outcome == "uncertain" and any("drift" in x.lower() for x in r.reasons)


def test_optical_uncertain_on_noise():
    r = evaluate_optical(samples(0.20, 0.40, 0.20, noise=0.08))
    assert r.outcome == "uncertain"


def test_optical_uncertain_with_too_few_samples():
    r = evaluate_optical(samples(0.2, 0.4, 0.2, n=2))
    assert r.outcome == "uncertain"


def prior_pass(design):
    from buildloop.models import VerificationResult

    return VerificationResult(design_id=design.id, passed=True, level=VerificationLevel.SENSOR_OBSERVED, evidence=[], summary="")


def test_optical_cannot_upgrade_simulated(inventory):
    d = build_phone_sensor_light(inventory)
    res = apply_optical(prior_pass(d), d, evaluate_optical(samples(0.2, 0.5, 0.2, 0.004)), simulated=True, acked=True)
    assert res.level == VerificationLevel.SENSOR_OBSERVED and "SIMULATED" in res.summary


def test_optical_upgrades_real_device_to_physical(inventory):
    d = build_phone_sensor_light(inventory)
    res = apply_optical(prior_pass(d), d, evaluate_optical(samples(0.2, 0.5, 0.2, 0.004)), simulated=False, acked=True)
    assert res.level == VerificationLevel.PHYSICAL_VERIFIED


def test_optical_alone_never_reaches_physical(inventory):
    d = build_phone_sensor_light(inventory)
    res = apply_optical(None, d, evaluate_optical(samples(0.2, 0.5, 0.2, 0.004)), simulated=False, acked=True)
    assert res.level == VerificationLevel.OPTICAL_OBSERVED


def test_optical_contradiction_when_not_seen(inventory):
    d = build_phone_sensor_light(inventory)
    res = apply_optical(prior_pass(d), d, evaluate_optical(samples(0.2, 0.2, 0.2, 0.001)), simulated=False, acked=True)
    assert res.contradictions and res.level == VerificationLevel.SENSOR_OBSERVED and res.passed is None


# ---- metrics ---------------------------------------------------------------------------------
def test_metrics_keep_provenance_separate():
    m = Metrics()
    m.record("serial.latency_ms", 10, provenance="simulated")
    m.record("serial.latency_ms", 30, provenance="live")
    s = m.summary()["serial.latency_ms"]["by_provenance"]
    assert s["simulated"]["mean"] == 10 and s["live"]["mean"] == 30


# ---- Meta request shapes (mock transport; proves our wire format, not Meta's behaviour) -----------
def mock_client(monkeypatch, handler):
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr("buildloop.providers.meta.httpx.AsyncClient", Patched)


async def test_muse_tool_loop_uses_previous_response_id_and_flat_tools(monkeypatch, inventory):
    bodies = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        bodies.append(body)
        assert req.headers["authorization"] == "Bearer test-key"
        if len(bodies) == 1:
            return httpx.Response(200, json={"id": "resp_1", "output": [{"type": "function_call", "name": "propose_circuit", "arguments": json.dumps({"sensor_source": "phone_camera_brightness"}), "call_id": "c1", "status": "completed"}], "usage": {"input_tokens": 50, "output_tokens": 20}})
        return httpx.Response(200, json={"id": "resp_2", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}], "usage": {"input_tokens": 70, "output_tokens": 10}})

    mock_client(monkeypatch, handler)
    m = Metrics()
    mu = MetaReasoning(Settings(_env_file=None, MODEL_API_KEY="test-key", META_MIN_INTERVAL_S=0), metrics=m)
    out = await mu.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert out[0].source == "muse-spark" and out[0].behavior.sensor_source == "phone_camera_brightness"
    assert bodies[0]["model"] == "muse-spark-1.3" and "previous_response_id" not in bodies[0]
    assert all("function" not in t for t in bodies[0]["tools"]) and bodies[0]["tools"][0]["type"] == "function" and "name" in bodies[0]["tools"][0]
    assert bodies[1]["previous_response_id"] == "resp_1"
    assert bodies[1]["input"] == [{"type": "function_call_output", "call_id": "c1", "output": bodies[1]["input"][0]["output"]}]
    assert "muse.input_tokens" in m.summary() and "muse.latency_ms" in m.summary()


async def test_muse_unknown_tool_call_is_rejected_and_traced(monkeypatch, inventory):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"id": "r1", "output": [{"type": "function_call", "name": "set_led", "arguments": "{\"pin\":25,\"on\":true}", "call_id": "x", "status": "completed"}]})
        return httpx.Response(200, json={"id": "r2", "output": []})

    mock_client(monkeypatch, handler)
    mu = MetaReasoning(Settings(_env_file=None, MODEL_API_KEY="k", META_MIN_INTERVAL_S=0))
    with pytest.raises(ProviderError) as e:
        await mu.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert e.value.code == "no_valid_proposal"
    assert any(t.get("tool_trace") and t["tool_trace"][0]["result"].get("rejected") for t in mu.last_trace)


async def test_error_envelope_is_surfaced(monkeypatch):
    mock_client(monkeypatch, lambda req: httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "bad schema", "code": "x"}}))
    c = MetaClient(Settings(_env_file=None, MODEL_API_KEY="k", META_MIN_INTERVAL_S=0), __import__("buildloop.providers.base", fromlist=["Usage"]).Usage())
    with pytest.raises(ProviderError) as e:
        await c.json({"model": "m", "input": "x"})
    assert e.value.code == "bad_request" and "bad schema" in e.value.message


async def test_rate_limit_retries_then_succeeds(monkeypatch):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}) if n["i"] == 1 else httpx.Response(200, json={"id": "ok", "output": []})

    mock_client(monkeypatch, handler)
    import asyncio

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    c = MetaClient(Settings(_env_file=None, MODEL_API_KEY="k", META_MIN_INTERVAL_S=0), __import__("buildloop.providers.base", fromlist=["Usage"]).Usage())
    assert (await c.json({"model": "m", "input": "x"}))["id"] == "ok" and n["i"] == 2


# ---- HTTP: diagnostics, approval gate, metrics, phone ------------------------------------------------
@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'p.db'}", MODEL_API_KEY=""))) as c:
        yield c


def test_diagnostics_is_read_only(client, monkeypatch):
    opened = []
    monkeypatch.setattr("buildloop.hardware.transport.serial.Serial", lambda *a, **k: opened.append(a))
    d = client.get("/api/hardware/diagnostics").json()
    assert d["opened_any_port"] is False and opened == []
    assert d["expected_protocol"]["version"] == 1 and d["required_board_profile"]["led_pin"] == 25 and d["wiring"]


def test_serial_connect_requires_explicit_approval(client, monkeypatch):
    from buildloop.hardware.transport import PortInfo

    monkeypatch.setattr("buildloop.main.list_ports", lambda: [PortInfo("COM99", "test", "")])
    opened = []
    monkeypatch.setattr("buildloop.hardware.transport.serial.Serial", lambda *a, **k: opened.append(a))
    r = client.post("/api/hardware/connect", json={"port": "COM99"})
    assert r.status_code == 400 and r.json()["code"] == "approval_required"
    r = client.post("/api/hardware/connect", json={"port": "COM99", "approve_open_port": True, "confirm_port": "COM98"})
    assert r.json()["code"] == "approval_required"
    assert opened == []


def test_metrics_endpoint_and_phone_status(client):
    assert client.get("/api/metrics").json()["count"] == 0
    assert client.get("/api/phone/x/status").json()["connected"] is False
    jpg = client.get("/api/perception/demo-frame.jpg").content
    assert client.post("/api/phone/x/snapshot", files={"image": ("s.jpg", jpg, "image/jpeg")}).json()["bytes"] > 100
    assert client.get("/api/phone/x/status").json()["connected"] is True
    assert client.get("/api/phone/x/snapshot.jpg").content == jpg


def test_scan_records_simulated_metrics(client):
    pid = client.post("/api/projects", json={"name": "m", "mode": "demo"}).json()["id"]
    client.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    s = client.get("/api/metrics").json()["summary"]
    assert "sam.scan_detections" in s and list(s["sam.scan_detections"]["by_provenance"]) == ["simulated"]


def test_optical_http_flow_on_simulated_device(client):
    c = client
    pid = c.post("/api/projects", json={"name": "o", "mode": "demo"}).json()["id"]
    c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    for comp_ in c.get(f"/api/inventory/{pid}").json():
        patch = {"confirm": True}
        if comp_["type"] == "resistor":
            patch["properties"] = {"resistance_ohm": 220 if comp_["name"].endswith("A") else 10000}
        if comp_["type"] == "led":
            patch["properties"] = {"forward_v": 2.0}
        c.patch(f"/api/inventory/{pid}/{comp_['id']}", json=patch)
    ldr = next(x for x in c.get(f"/api/inventory/{pid}").json() if x["type"] == "photoresistor")
    c.patch(f"/api/inventory/{pid}/{ldr['id']}", json={"available": False})
    designs = c.post(f"/api/designs/{pid}/plan", json={}).json()
    phone = next(d for d in designs if d["behavior"]["sensor_source"] == "phone_camera_brightness")
    for s in c.post(f"/api/designs/{pid}/{phone['id']}/approve").json():
        c.post(f"/api/assembly/{pid}/{phone['id']}/{s['index']}/confirm")
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    c.post(f"/api/hardware/{pid}/authorize")
    for on, phase in ((False, "off_before"), (True, "on"), (False, "off_after")):
        assert c.post(f"/api/verification/{pid}/optical/command", json={"on": on, "phase": phase}).json()["led_reported"] is on
    s = []
    for phase, base in (("off_before", 0.2), ("on", 0.5), ("off_after", 0.2)):
        s += [{"t": i, "luma": base + (0.003 if i % 2 else -0.003), "phase": phase} for i in range(8)]
    v = c.post(f"/api/verification/{pid}/optical/evaluate", json={"samples": s}).json()
    assert v["level"] == "optical_output_observed" or v["level"] == "command_acknowledged"
    assert v["level"] != "physical_behavior_verified"  # simulated device can never be physically verified
    assert any(e["simulated"] for e in v["evidence"] if e["kind"] == "optical")
    _ = (comp, ComponentType)


async def test_plan_advances_stage_to_validate_without_forcing(tmp_path):
    """Regression (found by the browser e2e): plan() used a stale project and stayed in PLAN, so approve never reached GUIDE."""
    from buildloop.db import Store
    from buildloop.models import Mode, Stage
    from buildloop.providers import make_provider_factory
    from buildloop.workflow import Engine

    s = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 's.db'}", MODEL_API_KEY="")
    e = Engine(Store(s.DATABASE_URL), make_provider_factory(s))
    p = e.create_project("t", "g", Mode.DEMO, "esp32-devkitc-v4")
    await e.scan(p.id, b"x")
    for c in e.components(p.id):
        patch = {"confirm": True}
        if c.type == ComponentType.RESISTOR:
            patch["properties"] = {"resistance_ohm": 220 if c.name.endswith("A") else 10000}
        if c.type == ComponentType.LED:
            patch["properties"] = {"forward_v": 2.0}
        e.update_component(p.id, c.id, patch)
    designs = await e.plan(p.id)
    assert e.project(p.id).stage == Stage.VALIDATE
    e.approve_design(p.id, designs[0].id)
    assert e.project(p.id).stage == Stage.GUIDE


def test_two_demo_projects_do_not_share_components(client):
    """Regression (found by the browser e2e): fixed demo ids collided across projects."""
    ids = []
    for n in ("a", "b"):
        pid = client.post("/api/projects", json={"name": n, "mode": "demo"}).json()["id"]
        client.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
        ids.append((pid, {c["id"] for c in client.get(f"/api/inventory/{pid}").json()}))
    assert len(ids[0][1]) == 6 and len(ids[1][1]) == 6 and not (ids[0][1] & ids[1][1])


# ---- open-weight backend (explicit config, labelled, same closed tool set) ----------------------------------
async def test_open_weight_chat_tool_loop_and_labelling(monkeypatch, inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    bodies = []

    def handler(req: httpx.Request):
        bodies.append((str(req.url), json.loads(req.content)))
        if len(bodies) == 1:
            call = {"id": "t1", "type": "function", "function": {"name": "propose_circuit", "arguments": json.dumps({"sensor_source": "phone_camera_brightness"})}}
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}], "usage": {"prompt_tokens": 5}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    mock_client(monkeypatch, handler)
    s = Settings(_env_file=None, MODEL_API_KEY="", OPEN_WEIGHT_BASE_URL="https://partner.example/v1", OPEN_WEIGHT_API_KEY="k", OPEN_WEIGHT_MODEL="muse-spark-open-x", META_MIN_INTERVAL_S=0)
    ow = OpenWeightReasoning(s)
    out = await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert out[0].source == "open-weight:muse-spark-open-x@partner.example"  # never labelled as Muse Spark API
    assert bodies[0][0].endswith("/v1/chat/completions") and bodies[0][1]["tools"][0]["function"]["name"]
    assert bodies[1][1]["messages"][-1]["role"] == "tool" and bodies[1][1]["messages"][-1]["tool_call_id"] == "t1"


async def test_open_weight_unconfigured_is_an_error_not_a_fallback(inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    ow = OpenWeightReasoning(Settings(_env_file=None, MODEL_API_KEY="has-meta-key", REASONING_BACKEND="open_weight"))
    with pytest.raises(ProviderError) as e:
        await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert e.value.code == "no_api_key"


# ---- agent control loop -----------------------------------------------------------------------------------
def _conf(client, pid):
    for c in client.get(f"/api/inventory/{pid}").json():
        patch = {"confirm": True}
        if c["type"] == "resistor":
            patch["properties"] = {"resistance_ohm": 220 if c["name"].endswith("A") else 10000}
        if c["type"] == "led":
            patch["properties"] = {"forward_v": 2.0}
        client.patch(f"/api/inventory/{pid}/{c['id']}", json=patch)


def test_agent_walks_the_loop_and_replans(client):
    c = client
    pid = c.post("/api/projects", json={"name": "a", "mode": "demo"}).json()["id"]
    a = c.get(f"/api/agent/{pid}").json()
    assert a["stage"] == "OBSERVE" and a["next_action"]["kind"] == "scan"
    c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    a = c.get(f"/api/agent/{pid}").json()
    assert a["next_action"]["kind"] == "confirm_inventory" and a["blockers"] and a["pending_approvals"]
    _conf(c, pid)
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "plan"
    designs = c.post(f"/api/designs/{pid}/plan", json={}).json()
    a = c.get(f"/api/agent/{pid}").json()
    assert a["next_action"]["kind"] == "approve_design" and "Photoresistor" in a["next_action"]["label"]  # self-contained preferred
    ldr = next(d for d in designs if d["behavior"]["sensor_source"] == "photoresistor_adc")
    steps = c.post(f"/api/designs/{pid}/{ldr['id']}/approve").json()
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "confirm_step"
    for s in steps:
        c.post(f"/api/assembly/{pid}/{ldr['id']}/{s['index']}/confirm")
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "connect_hardware"
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "authorize"
    c.post(f"/api/hardware/{pid}/authorize")
    assert c.get(f"/api/agent/{pid}").json()["next_action"]["kind"] == "run_test"
    # reality changes -> agent must say replan and name the cause
    ldr_c = next(x for x in c.get(f"/api/inventory/{pid}").json() if x["type"] == "photoresistor")
    c.patch(f"/api/inventory/{pid}/{ldr_c['id']}", json={"available": False})
    a = c.get(f"/api/agent/{pid}").json()
    assert a["stage"] == "REDESIGN" and a["next_action"]["kind"] == "replan"
    assert any("invalidated" in t and "photoresistor" in t for t in a["replan_triggers"])
    assert "VALIDATE" in a["allowed_next"]  # explicit state machine: redesign must be validated before use


def test_agent_flags_contradictory_verification_as_replan_trigger(client):
    c = client
    pid = c.post("/api/projects", json={"name": "v", "mode": "demo"}).json()["id"]
    c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    _conf(c, pid)
    ldr = next(d for d in c.post(f"/api/designs/{pid}/plan", json={}).json() if d["behavior"]["sensor_source"] == "photoresistor_adc")
    for s in c.post(f"/api/designs/{pid}/{ldr['id']}/approve").json():
        c.post(f"/api/assembly/{pid}/{ldr['id']}/{s['index']}/confirm")
    c.post("/api/hardware/connect", json={"simulated": True, "project_id": pid})
    c.post(f"/api/hardware/{pid}/authorize")
    c.post(f"/api/verification/{pid}/photoresistor/start")
    c.post("/api/hardware/sim/ambient", json={"value": 0.9})
    # lie about expectations: claim the LED should be ON in bright light -> contradiction
    c.post(f"/api/verification/{pid}/phase", json={"label": "bright", "expected_led": True, "seconds": 0.6})
    c.post("/api/hardware/sim/ambient", json={"value": 0.1})
    c.post(f"/api/verification/{pid}/phase", json={"label": "dark", "expected_led": True, "seconds": 0.6})
    v = c.post(f"/api/verification/{pid}/photoresistor/finish").json()
    assert v["passed"] is False
    a = c.get(f"/api/agent/{pid}").json()
    assert a["next_action"]["kind"] == "replan" and any("contradicted" in t for t in a["replan_triggers"])


async def test_open_weight_json_fallback_when_model_has_no_tool_calling(monkeypatch, inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:  # model ignores tools, answers in prose
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "I would use a light sensor."}}]})
        reply = 'Sure: {"sensor_sources": ["phone_camera_brightness", "rm -rf /", "photoresistor_adc"]}'
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": reply}}], "usage": {"prompt_tokens": 9, "completion_tokens": 7}})

    mock_client(monkeypatch, handler)
    s = Settings(_env_file=None, OPEN_WEIGHT_BASE_URL="https://router.huggingface.co/v1", OPEN_WEIGHT_API_KEY="k", OPEN_WEIGHT_MODEL="meta-models/Muse-Glimmer-30B", META_MIN_INTERVAL_S=0)
    ow = OpenWeightReasoning(s)
    out = await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert {d.behavior.sensor_source for d in out} == {"phone_camera_brightness", "photoresistor_adc"}  # junk entry ignored
    assert all(d.source == "open-weight:meta-models/Muse-Glimmer-30B@router.huggingface.co" for d in out)
    assert any(t.get("mode") == "json_fallback" for t in ow.last_trace)


def test_env_example_copied_verbatim_loads(tmp_path, monkeypatch):
    """Regression: blank numeric keys in .env.example must not crash Settings."""
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / ".env.example"
    dst = tmp_path / ".env"
    shutil.copy(src, dst)
    s = Settings(_env_file=dst)
    assert not s.meta_configured and not s.open_weight_configured
    assert s.OPEN_WEIGHT_PRICE_IN_PER_M is None and s.REASONING_BACKEND == "meta_api"


async def test_open_weight_records_tokens_and_estimated_cost_only_with_prices(monkeypatch, inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    def handler(req):
        call = {"id": "t1", "type": "function", "function": {"name": "propose_circuit", "arguments": json.dumps({"sensor_source": "phone_camera_brightness"})}}
        if not hasattr(handler, "n"):
            handler.n = 1
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"prompt_tokens": 2000, "completion_tokens": 100}})

    mock_client(monkeypatch, handler)
    base = {"OPEN_WEIGHT_BASE_URL": "https://p.example/v1", "OPEN_WEIGHT_API_KEY": "k", "OPEN_WEIGHT_MODEL": "m", "META_MIN_INTERVAL_S": 0}
    m = Metrics()
    await OpenWeightReasoning(Settings(_env_file=None, **base), metrics=m).propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    s = m.summary()
    live = s["openweight.input_tokens"]["by_provenance"]["live"]
    assert live["count"] == 2 and live["mean"] * live["count"] == 3000  # chat-format usage keys are read
    assert "openweight.cost_usd_estimate" not in s  # no prices => no invented cost
    handler.__dict__.clear()
    m2 = Metrics()
    await OpenWeightReasoning(Settings(_env_file=None, **base, OPEN_WEIGHT_PRICE_IN_PER_M=0.2, OPEN_WEIGHT_PRICE_OUT_PER_M=0.6), metrics=m2).propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    est = m2.summary()["openweight.cost_usd_estimate"]["by_provenance"]["live"]["mean"]
    assert est == pytest.approx((3000 * 0.2 + 600 * 0.6) / 1e6)


async def test_payment_required_is_reported_not_simulated(monkeypatch, inventory):
    """Real evidence (2026-10-28): the HF router answered 402 for an account without credits."""
    from buildloop.providers.open_weight import OpenWeightReasoning

    mock_client(monkeypatch, lambda req: httpx.Response(402, json={"error": "You have no remaining credits."}))
    ow = OpenWeightReasoning(Settings(_env_file=None, HF_TOKEN="t", META_MIN_INTERVAL_S=0))
    assert ow.name == "open-weight:meta-models/Muse-Glimmer-30B@router.huggingface.co"
    with pytest.raises(ProviderError) as e:
        await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert e.value.code == "payment_required" and "Open-weight inference partner" in e.value.message


# ---- VLM perception: real camera frame -> multimodal model -> structured inventory (mock transport; live run needs credits) ----
MESSY_MODEL_OUTPUT = """Here is what I can see:
```json
{"components": [
  {"type": "led", "label": "red LED", "box": [0.40, 0.20, 0.45, 0.30], "confidence": 0.9},
  {"type": "resistor", "label": "resistor", "box": [0.5, 0.5, 0.6, 0.55], "confidence": 0.55},
  {"type": "flux capacitor", "label": "???", "box": [2, 2, 3, 3], "confidence": 7},
  {"type": "esp32", "label": "dev board", "box": null, "confidence": 0.8},
  "garbage"
]}
```"""


def test_parse_vlm_inventory_is_strict():
    from buildloop.models import Provenance
    from buildloop.providers.open_weight import parse_vlm_inventory

    comps = parse_vlm_inventory(MESSY_MODEL_OUTPUT, 640, 480)
    assert [c.type.value for c in comps] == ["led", "resistor", "unknown", "esp32"]
    led, res, weird, esp = comps
    assert led.bbox and (led.bbox.x1, led.bbox.y1) == (256.0, 96.0) and led.status == Provenance.DETECTED
    assert res.status == Provenance.UNCERTAIN and res.properties["resistance_ohm"] is None  # low confidence + value never guessed
    assert weird.bbox is None and weird.confidence is None and weird.status == Provenance.UNCERTAIN  # out-of-range box/confidence dropped
    assert esp.bbox is None and all(c.mask_path is None for c in comps)  # no masks claimed
    assert parse_vlm_inventory("no json at all", 10, 10) == [] and parse_vlm_inventory('{"components": 5}', 10, 10) == []


async def test_vlm_scan_uses_image_url_data_and_labels_provenance(monkeypatch):
    import cv2
    import numpy as np

    from buildloop.providers.open_weight import OpenWeightReasoning, VlmPerception

    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": MESSY_MODEL_OUTPUT}}], "usage": {"prompt_tokens": 800, "completion_tokens": 120}})

    mock_client(monkeypatch, handler)
    _, buf = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))
    vp = VlmPerception(OpenWeightReasoning(Settings(_env_file=None, HF_TOKEN="t", META_MIN_INTERVAL_S=0)))
    comps = await vp.scan_scene(buf.tobytes())
    part = seen["body"]["messages"][0]["content"]
    assert part[0]["type"] == "text" and part[1]["type"] == "image_url" and part[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen["body"]["model"] == "meta-models/Muse-Glimmer-30B" and len(comps) == 4
    assert vp.name == "vlm:meta-models/Muse-Glimmer-30B@router.huggingface.co" and "SAM" not in vp.name


def test_real_project_scan_with_vlm_then_manual_correction(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    mock_client(monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": MESSY_MODEL_OUTPUT}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}))
    s = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'v.db'}", HF_TOKEN="t", PERCEPTION_BACKEND="vlm", REASONING_BACKEND="open_weight", META_MIN_INTERVAL_S=0)
    _, buf = cv2.imencode(".jpg", np.random.default_rng(1).integers(0, 255, (240, 320, 3), dtype=np.uint8))  # textured, >=160px
    with TestClient(create_app(s)) as c:
        h = c.get("/api/health").json()
        assert h["perception_backend"] == "vlm" and h["reasoning_backend"] == "open_weight" and h["open_weight_model"] == "meta-models/Muse-Glimmer-30B"
        pid = c.post("/api/projects", json={"name": "r", "mode": "real"}).json()["id"]
        r = c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", buf.tobytes(), "image/jpeg")}).json()
        assert r["provider"].startswith("vlm:") and r["live"] is True and r["detections"] == []
        assert r["frame"]["width"] == 320 and r["frame"]["resized"] is False
        inv = c.get(f"/api/inventory/{pid}").json()
        assert len(inv) == 4 and all(x["status"] in ("detected", "uncertain") for x in inv) and all(x["mask_path"] is None for x in inv)
        wrong = next(x for x in inv if x["type"] == "unknown")
        fixed = c.patch(f"/api/inventory/{pid}/{wrong['id']}", json={"type": "photoresistor", "confirm": True}).json()
        assert fixed["type"] == "photoresistor" and fixed["status"] == "user_confirmed"
        assert sum(1 for x in c.get(f"/api/inventory/{pid}").json() if x["status"] == "user_confirmed") == 1
        m = c.get("/api/metrics").json()["summary"]
        assert "vlm.scan_total_ms" in m and list(m["vlm.scan_total_ms"]["by_provenance"]) == ["live"]


def test_real_scan_payment_required_surfaces_and_never_falls_back(tmp_path, monkeypatch):
    mock_client(monkeypatch, lambda req: httpx.Response(402, json={"error": "You have no remaining credits."}))
    s = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'p.db'}", HF_TOKEN="t", PERCEPTION_BACKEND="vlm", META_MIN_INTERVAL_S=0)
    with TestClient(create_app(s)) as c:
        pid = c.post("/api/projects", json={"name": "r", "mode": "real"}).json()["id"]
        r = c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"\xff\xd8x", "image/jpeg")})
        assert r.status_code == 409 and r.json()["code"] in ("payment_required", "bad_image")
        assert c.get(f"/api/inventory/{pid}").json() == []  # nothing simulated was stored


# ---- image preprocessing ----------------------------------------------------------------------------------
def _jpeg(arr):
    import cv2

    return cv2.imencode(".jpg", arr)[1].tobytes()


def test_preprocess_downscales_and_reports_quality():
    import cv2
    import numpy as np

    from buildloop.imaging import ImageError, preprocess

    rng = np.random.default_rng(0)
    big = rng.integers(0, 255, (1800, 2400, 3), dtype=np.uint8)
    out, rep = preprocess(_jpeg(big))
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 1280 and rep.resized and (rep.orig_width, rep.orig_height) == (2400, 1800) and rep.usable
    blank = np.full((480, 640, 3), 128, np.uint8)  # flat grey = no detail => blurry warning
    _, r2 = preprocess(_jpeg(blank))
    assert any("blurry" in w for w in r2.warnings) and not r2.usable
    _, dark = preprocess(_jpeg(np.zeros((300, 300, 3), np.uint8)))
    assert any("dark" in w for w in dark.warnings)
    with pytest.raises(ImageError):
        preprocess(b"not an image")
    with pytest.raises(ImageError):
        preprocess(_jpeg(np.zeros((40, 40, 3), np.uint8)))


def test_preprocess_strips_exif_metadata():
    import numpy as np

    from buildloop.imaging import preprocess

    base = _jpeg(np.random.default_rng(2).integers(0, 255, (200, 300, 3), dtype=np.uint8))
    exif = b"Exif\x00\x00GPS-LAT-12.9716N"
    with_exif = base[:2] + b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif + base[2:]
    out, _ = preprocess(with_exif)
    assert b"GPS-LAT" not in out and b"Exif" not in out


# ---- Baseten preset (identifier/endpoint from Baseten docs; live availability unverified) -----------------------
def test_baseten_preset_and_priority():
    s = Settings(_env_file=None, BASETEN_API_KEY="b")
    assert (s.ow_provider, s.ow_base_url, s.ow_model) == ("baseten", "https://inference.baseten.co/v1", "meta-models/muse-spark-open-1026")
    assert Settings(_env_file=None, BASETEN_API_KEY="b", OPEN_WEIGHT_MODEL="meta-models/muse-spark-open").ow_model == "meta-models/muse-spark-open"  # configurable
    both = Settings(_env_file=None, BASETEN_API_KEY="b", HF_TOKEN="h")
    assert both.ow_provider == "baseten" and both.ow_key == "b"  # explicit preference order, no silent mixing of keys
    hf = Settings(_env_file=None, BASETEN_API_KEY="b", HF_TOKEN="h", OPEN_WEIGHT_PROVIDER="huggingface")
    assert hf.ow_provider == "huggingface" and hf.ow_key == "h" and hf.ow_model == "meta-models/Muse-Glimmer-30B"
    custom = Settings(_env_file=None, OPEN_WEIGHT_BASE_URL="https://x/v1", OPEN_WEIGHT_API_KEY="k", OPEN_WEIGHT_MODEL="m")
    assert custom.open_weight_configured and custom.ow_provider == "custom"


async def test_baseten_request_shape_no_json_mode_params(monkeypatch, inventory):
    """Baseten: Muse Spark Open does not support JSON mode/structured outputs and rejects n>1. Our requests must not send them."""
    from buildloop.providers.open_weight import OpenWeightReasoning

    seen = []

    def handler(req):
        seen.append((str(req.url), req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"sensor_sources": ["phone_camera_brightness"]}'}}]})

    mock_client(monkeypatch, handler)
    ow = OpenWeightReasoning(Settings(_env_file=None, BASETEN_API_KEY="bk", META_MIN_INTERVAL_S=0))
    out = await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert ow.name == "open-weight:meta-models/muse-spark-open-1026@inference.baseten.co" and out[0].source.startswith("open-weight:meta-models/muse-spark-open-1026@inference.baseten.co")
    assert all(u == "https://inference.baseten.co/v1/chat/completions" and a == "Bearer bk" for u, a, _ in seen)
    assert all("response_format" not in b and "n" not in b for _, _, b in seen)


# ---- editable inventory ---------------------------------------------------------------------------------------
def test_discard_false_detection_and_rename(client):
    pid = client.post("/api/projects", json={"name": "d", "mode": "demo"}).json()["id"]
    client.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", b"x", "image/jpeg")})
    inv = client.get(f"/api/inventory/{pid}").json()
    victim = inv[0]
    assert client.patch(f"/api/inventory/{pid}/{victim['id']}", json={"name": "My ESP32 board"}).json()["name"] == "My ESP32 board"
    r = client.delete(f"/api/inventory/{pid}/{victim['id']}")
    assert r.status_code == 200 and len(client.get(f"/api/inventory/{pid}").json()) == 5
    assert client.delete(f"/api/inventory/{pid}/{victim['id']}").status_code == 404  # already gone
    assert client.delete(f"/api/inventory/other_project/{inv[1]['id']}").status_code == 404  # cannot discard another project's part
    assert any(m["name"] == "detection.false_positive" for m in client.get("/api/metrics/raw").json())
    assert any("discarded false detection" in r["summary"] for r in client.get(f"/api/revisions/{pid}").json())


# ---- camera -> VLM pipeline replayed from a RECORDED-STYLE fixture (synthetic; not live Meta AI) --------------------
async def test_vlm_pipeline_replays_fixture_and_never_claims_live_meta(monkeypatch, tmp_path):
    from pathlib import Path

    import cv2
    import numpy as np

    fx = json.loads((Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "vlm" / "synthetic_vlm_response.json").read_text(encoding="utf-8"))
    assert fx["_provenance"].startswith("SYNTHETIC")
    mock_client(monkeypatch, lambda req: httpx.Response(200, json={k: v for k, v in fx.items() if not k.startswith("_")}))
    s = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'f.db'}", HF_TOKEN="t", PERCEPTION_BACKEND="vlm", META_MIN_INTERVAL_S=0)
    frame = cv2.imencode(".jpg", np.random.default_rng(3).integers(0, 255, (900, 1600, 3), dtype=np.uint8))[1].tobytes()
    with TestClient(create_app(s)) as c:
        pid = c.post("/api/projects", json={"name": "fx", "mode": "real"}).json()["id"]
        r = c.post(f"/api/perception/{pid}/scan", files={"image": ("f.jpg", frame, "image/jpeg")}).json()
        assert r["frame"]["resized"] is True and r["frame"]["width"] == 1280  # preprocessing ran before the (mocked) model
        inv = c.get(f"/api/inventory/{pid}").json()
        assert [x["type"] for x in inv] == ["esp32", "led", "resistor"]
        assert all(x["status"] in ("detected", "uncertain") and x["mask_path"] is None for x in inv)
        led = next(x for x in inv if x["type"] == "led")
        assert led["bbox"]["x1"] == pytest.approx(0.44 * 1280) and led["bbox"]["img_w"] == 1280
        assert "sam" not in r["provider"].lower() and "meta-api" not in r["provider"].lower()


async def test_provider_that_rejects_tools_field_falls_back_to_json_choice(monkeypatch, inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "tools are not supported for this model"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"sensor_sources": ["phone_camera_brightness"]}'}}]})

    mock_client(monkeypatch, handler)
    ow = OpenWeightReasoning(Settings(_env_file=None, BASETEN_API_KEY="k", META_MIN_INTERVAL_S=0))
    out = await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert out and out[0].behavior.sensor_source == "phone_camera_brightness" and ow.tools_supported is False
    assert any(t.get("mode") == "tools_rejected_by_provider" for t in ow.last_trace)  # the downgrade is visible, not silent
    assert sum("tools" in b for b in seen) == 1 and all("response_format" not in b for b in seen)


async def test_unknown_model_slug_is_model_not_found_not_substituted(monkeypatch, inventory):
    from buildloop.providers.open_weight import OpenWeightReasoning

    seen = []

    def handler(req):
        seen.append(json.loads(req.content)["model"])
        return httpx.Response(404, json={"error": {"type": "not_found", "message": "model does not exist"}})

    mock_client(monkeypatch, handler)
    ow = OpenWeightReasoning(Settings(_env_file=None, BASETEN_API_KEY="k", OPEN_WEIGHT_MODEL="meta-models/muse-spark-open-1026", META_MIN_INTERVAL_S=0))
    with pytest.raises(ProviderError) as e:
        await ow.propose_designs(PlanContext(goal="g", board_id="esp32-devkitc-v4", inventory=inventory))
    assert e.value.code == "model_not_found" and "baseten_audit" in e.value.hint
    assert set(seen) == {"meta-models/muse-spark-open-1026"}  # never retried under another slug
