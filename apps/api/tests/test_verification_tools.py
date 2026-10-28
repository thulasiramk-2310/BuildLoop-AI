from buildloop.designs import build_adaptive_light
from buildloop.models import TelemetryRecord, VerificationLevel
from buildloop.tools import ToolContext, call_tool
from buildloop.verification import Phase, PhoneSample, evaluate_phone_light, evaluate_photoresistor


def tel(adc, led, source="hardware", n=5):
    return [TelemetryRecord(source=source, adc=adc, led_state=led) for _ in range(n)]


def design(inventory):
    return build_adaptive_light(inventory)  # threshold ~1515, dark = adc_low


def test_hardware_gpio_and_sensor_levels(inventory):
    d = design(inventory)
    phases = [Phase("bright", tel(3000, False), False), Phase("dark", tel(400, True), True)]
    r = evaluate_photoresistor(d, phases, ["run_test"])
    assert r.passed is True and r.level == VerificationLevel.SENSOR_OBSERVED
    assert not any(e.simulated for e in r.evidence)


def test_optical_evidence_required_for_physical_verified(inventory):
    d = design(inventory)
    phases = [Phase("bright", tel(3000, False), False, [0.20, 0.21]), Phase("dark", tel(400, True), True, [0.45, 0.47])]
    r = evaluate_photoresistor(d, phases, ["run_test"])
    assert r.level == VerificationLevel.PHYSICAL_VERIFIED and r.passed is True


def test_contradictory_optical_evidence_flagged(inventory):
    d = design(inventory)
    phases = [Phase("bright", tel(3000, False), False, [0.20]), Phase("dark", tel(400, True), True, [0.20])]
    r = evaluate_photoresistor(d, phases, ["run_test"])
    assert r.contradictions and r.level != VerificationLevel.PHYSICAL_VERIFIED
    assert r.passed is None  # inconclusive, not a pass


def test_device_state_contradicting_sensor_rule(inventory):
    d = design(inventory)
    phases = [Phase("bright", tel(3000, False), False), Phase("dark", tel(400, False), True)]
    r = evaluate_photoresistor(d, phases, ["run_test"])
    assert r.passed is False and r.contradictions


def test_simulated_capped_and_labelled(inventory):
    d = design(inventory)
    phases = [Phase("bright", tel(3000, False, "simulated"), False, [0.2]), Phase("dark", tel(400, True, "simulated"), True, [0.5])]
    r = evaluate_photoresistor(d, phases, ["run_test"])
    assert r.level == VerificationLevel.SENSOR_OBSERVED  # even with optical samples
    assert all(e.simulated for e in r.evidence if e.kind != "optical")
    assert "SIMULATED" in r.summary


def test_no_data_is_not_a_pass(inventory):
    r = evaluate_photoresistor(design(inventory), [Phase("bright", [], False)], [])
    assert r.passed is None and r.level == VerificationLevel.NONE


def test_phone_wrong_command_is_contradiction(inventory):
    d = design(inventory)
    d.behavior.sensor_source = "phone_camera_brightness"
    d.behavior.threshold = 0.35
    samples = [PhoneSample(0.8, False, False, True), PhoneSample(0.1, False, False, True)]  # dark but LED off
    r = evaluate_phone_light(d, samples, simulated=False)
    assert r.passed is False


def test_phone_hardware_run_not_marked_optical(inventory):
    d = design(inventory)
    d.behavior.threshold = 0.35
    samples = [PhoneSample(0.8, False, False, True), PhoneSample(0.1, True, True, True), PhoneSample(0.9, False, False, True)]
    r = evaluate_phone_light(d, samples, simulated=False)
    assert r.passed is True and r.level == VerificationLevel.SENSOR_OBSERVED
    assert "not independently observed" in r.summary


def test_unauthorized_model_tool_rejected(inventory):
    ctx = ToolContext(inventory, "esp32-devkitc-v4", "goal")
    out = call_tool(ctx, "set_led", {"pin": 25, "on": True})  # hardware action smuggled through the tool channel
    assert out.get("rejected") is True
    out = call_tool(ctx, "run_shell", {"cmd": "rm -rf /"})
    assert out.get("rejected") is True
    assert [t["tool"] for t in ctx.trace] == ["set_led", "run_shell"]


def test_propose_circuit_is_validated_by_trusted_code(inventory):
    ctx = ToolContext(inventory, "esp32-devkitc-v4", "goal")
    out = call_tool(ctx, "propose_circuit", {"sensor_source": "photoresistor_adc"})
    assert out["design_id"] in ctx.candidates and out["validation"]
    bad = call_tool(ctx, "propose_circuit", {"sensor_source": "magic"})
    assert "error" in bad


def test_tools_expose_board_capabilities(inventory):
    ctx = ToolContext(inventory, "esp32-devkitc-v4", "goal")
    caps = call_tool(ctx, "get_board_capabilities", {})
    assert 34 in caps["input_only"] and 34 in caps["adc_pins"] and caps["five_v_tolerant"] is False
    assert call_tool(ctx, "validate_connection", {"pin": 34, "role": "output"})["ok"] is False
