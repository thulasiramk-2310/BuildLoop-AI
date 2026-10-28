"""Deterministic design generators and assembly guidance.

These produce schema-valid candidates that the validator then checks. They are the fallback / demo
planner and the reference the Muse Spark proposals are compared against.
"""
from __future__ import annotations

from .boards import BoardProfile, get_board
from .models import (
    AssemblyStep,
    ComponentInstance,
    ComponentType,
    Connection,
    DesignCandidate,
    FirmwareBehavior,
    PinDefinition,
)
from .validation import validate_design

LED_R_MAX_OHM = 1000.0  # resistors at or below this are LED-limiter candidates


def default_pins(t: ComponentType) -> list[PinDefinition]:
    return {
        ComponentType.LED: [PinDefinition(name="anode", function="anode"), PinDefinition(name="cathode", function="cathode")],
        ComponentType.RESISTOR: [PinDefinition(name="a", function="terminal"), PinDefinition(name="b", function="terminal")],
        ComponentType.PHOTORESISTOR: [PinDefinition(name="a", function="terminal"), PinDefinition(name="b", function="terminal")],
        ComponentType.PUSHBUTTON: [PinDefinition(name="a", function="terminal"), PinDefinition(name="b", function="terminal")],
    }.get(t, [])


def _avail(inv: list[ComponentInstance], t: ComponentType) -> list[ComponentInstance]:
    return [c for c in inv if c.available and c.type == t]


def _pick_board(inv: list[ComponentInstance]) -> ComponentInstance | None:
    for t in (ComponentType.ESP32, ComponentType.ARDUINO):
        found = _avail(inv, t)
        if found:
            return found[0]
    return None


def _assign_resistors(inv: list[ComponentInstance], need_divider: bool) -> dict[str, ComponentInstance]:
    """Pick LED limiter (small value) and divider (larger value). Unknown values keep roles by order."""
    rs = _avail(inv, ComponentType.RESISTOR)

    def ohm(c: ComponentInstance) -> float | None:
        v = c.properties.get("resistance_ohm")
        return float(v) if isinstance(v, (int, float)) else None

    known = sorted([c for c in rs if ohm(c) is not None], key=lambda c: ohm(c))  # type: ignore[arg-type,return-value]
    unknown = [c for c in rs if ohm(c) is None]
    roles: dict[str, ComponentInstance] = {}
    pool = known + unknown
    small = next((c for c in known if (ohm(c) or 0) <= LED_R_MAX_OHM), None)
    if small is None and unknown:
        small = unknown[0]  # unverifiable value: validator reports UNKNOWN until the user confirms it
    elif small is None and pool:
        small = pool[0]
    if small:
        roles["led_limit"] = small
        pool = [c for c in pool if c.id != small.id]
    if need_divider and pool:
        roles["divider"] = pool[-1] if known and pool[-1] in known else pool[0]
    return roles


def _tag(c: ComponentInstance, role: str) -> ComponentInstance:
    c = c.model_copy(deep=True)
    c.properties = {**c.properties, "role": role}
    if not c.pins:
        c.pins = default_pins(c.type)
    return c


def build_adaptive_light(inv: list[ComponentInstance], board: BoardProfile | None = None, goal: str = "") -> DesignCandidate:
    """LED + LDR divider; LED turns on when dark. Missing parts raise nothing: validator reports BLOCKED."""
    bc = _pick_board(inv)
    board = board or get_board("esp32-devkitc-v4" if not bc or bc.type == ComponentType.ESP32 else "arduino-uno")
    bid = bc.id if bc else "board_missing"
    leds, ldrs = _avail(inv, ComponentType.LED), _avail(inv, ComponentType.PHOTORESISTOR)
    roles = _assign_resistors(inv, need_divider=True)
    comps: list[ComponentInstance] = []
    if bc:
        comps.append(bc)
    led = _tag(leds[0], "load") if leds else ComponentInstance(id="led_missing", type=ComponentType.LED, name="LED (missing)", pins=default_pins(ComponentType.LED))
    ldr = _tag(ldrs[0], "sensor") if ldrs else ComponentInstance(id="ldr_missing", type=ComponentType.PHOTORESISTOR, name="Photoresistor (missing)", pins=default_pins(ComponentType.PHOTORESISTOR))
    rl = _tag(roles["led_limit"], "led_limit") if "led_limit" in roles else ComponentInstance(id="rled_missing", type=ComponentType.RESISTOR, name="LED resistor (missing)", pins=default_pins(ComponentType.RESISTOR), properties={"role": "led_limit"})
    rd = _tag(roles["divider"], "divider") if "divider" in roles else ComponentInstance(id="rdiv_missing", type=ComponentType.RESISTOR, name="Divider resistor (missing)", pins=default_pins(ComponentType.RESISTOR), properties={"role": "divider"})
    comps += [led, ldr, rl, rd]
    lp, ap = board.demo_led_pin, board.demo_adc_pin
    lab_led, lab_adc = board.pin_label(lp), board.pin_label(ap)
    cons = [
        Connection(src=f"{bid}.{lab_led}", dst=f"{rl.id}.a", expected="LED drive signal through current limiter"),
        Connection(src=f"{rl.id}.b", dst=f"{led.id}.anode", expected="Series resistor to LED anode"),
        Connection(src=f"{led.id}.cathode", dst=f"{bid}.GND", kind="ground", expected="LED returns to ground"),
        Connection(src=f"{bid}.3V3", dst=f"{ldr.id}.a", kind="power", expected="Divider top at 3.3 V"),
        Connection(src=f"{ldr.id}.b", dst=f"{bid}.{lab_adc}", expected="Divider midpoint to ADC"),
        Connection(src=f"{ldr.id}.b", dst=f"{rd.id}.a", expected="Divider midpoint to fixed resistor"),
        Connection(src=f"{rd.id}.b", dst=f"{bid}.GND", kind="ground", expected="Divider bottom to ground"),
    ]
    d = DesignCandidate(
        title="Photoresistor automatic light",
        goal=goal or "Turn an LED on when the environment gets dark",
        board=board.id,
        requires=[bc.type if bc else ComponentType.ESP32, ComponentType.LED, ComponentType.RESISTOR, ComponentType.RESISTOR, ComponentType.PHOTORESISTOR],
        components=comps,
        connections=cons,
        behavior=FirmwareBehavior(sensor_source="photoresistor_adc", sensor_pin=ap, led_pin=lp, threshold=board.adc_max_counts * 0.37, dark_means="adc_low"),
        tradeoffs=["Self-contained: works with no host computer once flashed.", "Needs a divider resistor and a photoresistor."],
        source="deterministic",
    )
    d.validation = validate_design(d, inv)
    return d


def build_phone_sensor_light(inv: list[ComponentInstance], board: BoardProfile | None = None, goal: str = "") -> DesignCandidate:
    """LED only; the phone camera's calibrated brightness acts as the light sensor via the backend."""
    bc = _pick_board(inv)
    board = board or get_board("esp32-devkitc-v4" if not bc or bc.type == ComponentType.ESP32 else "arduino-uno")
    bid = bc.id if bc else "board_missing"
    leds = _avail(inv, ComponentType.LED)
    roles = _assign_resistors(inv, need_divider=False)
    led = _tag(leds[0], "load") if leds else ComponentInstance(id="led_missing", type=ComponentType.LED, name="LED (missing)", pins=default_pins(ComponentType.LED))
    rl = _tag(roles["led_limit"], "led_limit") if "led_limit" in roles else ComponentInstance(id="rled_missing", type=ComponentType.RESISTOR, name="LED resistor (missing)", pins=default_pins(ComponentType.RESISTOR), properties={"role": "led_limit"})
    comps = ([bc] if bc else []) + [led, rl]
    lab_led = board.pin_label(board.demo_led_pin)
    cons = [
        Connection(src=f"{bid}.{lab_led}", dst=f"{rl.id}.a", expected="LED drive signal through current limiter"),
        Connection(src=f"{rl.id}.b", dst=f"{led.id}.anode", expected="Series resistor to LED anode"),
        Connection(src=f"{led.id}.cathode", dst=f"{bid}.GND", kind="ground", expected="LED returns to ground"),
    ]
    d = DesignCandidate(
        title="Phone-camera brightness light",
        goal=goal or "Turn an LED on when the environment gets dark",
        board=board.id,
        requires=[bc.type if bc else ComponentType.ESP32, ComponentType.LED, ComponentType.RESISTOR],
        components=comps,
        connections=cons,
        behavior=FirmwareBehavior(sensor_source="phone_camera_brightness", sensor_pin=None, led_pin=board.demo_led_pin, threshold=0.35, dark_means="brightness_low"),
        tradeoffs=[
            "Needs the phone and laptop running; not self-contained.",
            "Camera auto-exposure affects readings; brightness is relative to calibration.",
        ],
        substitute_notes=[
            "The phone camera measures the scene it faces, not the light at the LED. This is a substitute sensor, not an identical replacement for the photoresistor.",
            "Brightness is a calibrated luminance estimate (0-1), not lux.",
        ],
        source="deterministic",
    )
    d.validation = validate_design(d, inv)
    return d


def alternatives_for_missing(inv: list[ComponentInstance], goal: str = "") -> list[DesignCandidate]:
    """Feasible redesigns given current availability. Only validated-non-blocked candidates are returned first."""
    cands = [build_adaptive_light(inv, goal=goal), build_phone_sensor_light(inv, goal=goal)]
    ok = [c for c in cands if c.overall.value != "BLOCKED"]
    return ok or cands


def generate_assembly_steps(d: DesignCandidate) -> list[AssemblyStep]:
    names = {c.id: c.name for c in d.components}
    by_dst = {c.id: c for c in d.connections}

    def describe(cid: str) -> str:
        c = by_dst[cid]
        def nm(ref: str) -> str:
            comp, pin = ref.split(".", 1)
            return f"{names.get(comp, comp)} {pin}"
        return f"{nm(c.src)} -> {nm(c.dst)}"

    steps = [
        AssemblyStep(index=1, title="Unplug USB and place parts", detail="With the board unpowered, seat the breadboard parts. Orient the LED: long leg = anode.", needs_confirmation=True)
    ]
    groups: list[tuple[str, list[Connection]]] = []
    led_cons = [c for c in d.connections if any(k in (c.src + c.dst) for k in ("anode", "cathode")) or c.expected.startswith(("LED drive", "Series resistor"))]
    sensor_cons = [c for c in d.connections if c not in led_cons]
    groups.append(("Wire the LED branch", led_cons))
    if sensor_cons:
        groups.append(("Wire the light-sensor divider", sensor_cons))
    for title, cons in groups:
        steps.append(
            AssemblyStep(
                index=len(steps) + 1,
                title=title,
                detail="; ".join(f"{describe(c.id)} ({c.expected})" for c in cons),
                connection_ids=[c.id for c in cons],
            )
        )
    steps.append(AssemblyStep(index=len(steps) + 1, title="Visual check before power", detail="Compare every wire to the plan. Nothing is assumed correct until you confirm. Then connect USB.", needs_confirmation=True))
    return steps
