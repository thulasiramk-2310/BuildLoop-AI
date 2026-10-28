"""Deterministic engineering rules. Independent of any LLM output."""
from __future__ import annotations

from .boards import BoardProfile, get_board
from .models import (
    ComponentInstance,
    ComponentType,
    DesignCandidate,
    Provenance,
    ValidationResult,
    VStatus,
)

LED_VF_DEFAULT = 2.0  # typical red LED; user must confirm for other colours
LED_MIN_USEFUL_MA = 1.0


def _r(rule: str, status: VStatus, message: str, fix: str = "", estimated: bool = False) -> ValidationResult:
    return ValidationResult(rule=rule, status=status, message=message, fix=fix, estimated=estimated)


def _pin_number(ref: str) -> int | None:
    """'board.GPIO25' -> 25 ; 'board.A0' -> 14 (Uno). None for non-GPIO pins."""
    pin = ref.split(".", 1)[1] if "." in ref else ref
    up = pin.upper()
    if up.startswith("GPIO") and up[4:].isdigit():
        return int(up[4:])
    if up.startswith("D") and up[1:].isdigit():
        return int(up[1:])
    if up.startswith("A") and up[1:].isdigit():
        return 14 + int(up[1:])
    return None


def _board_component(d: DesignCandidate) -> ComponentInstance | None:
    return next((c for c in d.components if c.type in (ComponentType.ESP32, ComponentType.ARDUINO)), None)


def check_required_components(d: DesignCandidate, inventory: list[ComponentInstance]) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    avail: dict[ComponentType, int] = {}
    for c in inventory:
        if c.available:
            avail[c.type] = avail.get(c.type, 0) + 1
    need: dict[ComponentType, int] = {}
    for t in d.requires:
        need[t] = need.get(t, 0) + 1
    for t, n in need.items():
        have = avail.get(t, 0)
        if have < n:
            out.append(
                _r(
                    "inventory.required_components",
                    VStatus.BLOCKED,
                    f"Design needs {n} x {t.value}; inventory has {have}.",
                    f"Add the missing {t.value} or choose a design that does not need it.",
                )
            )
    if not out:
        out.append(_r("inventory.required_components", VStatus.PASS, "All required components are in inventory."))
    unconfirmed = [c.name for c in inventory if c.available and c.status in (Provenance.DETECTED, Provenance.UNCERTAIN)]
    if unconfirmed:
        out.append(
            _r(
                "inventory.unconfirmed",
                VStatus.WARNING,
                f"Unconfirmed components: {', '.join(unconfirmed)}.",
                "Confirm each component in the inventory panel before building.",
            )
        )
    return out


def check_pins(d: DesignCandidate, board: BoardProfile) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    bc = _board_component(d)
    used: dict[int, str] = {}
    for con in d.connections:
        for ref in (con.src, con.dst):
            if bc and ref.startswith(bc.id + "."):
                n = _pin_number(ref)
                if n is None:
                    continue
                if n in board.reserved:
                    out.append(_r("pin.reserved", VStatus.BLOCKED, f"{board.pin_label(n)} is reserved (flash/USB).", "Use another pin."))
                if n in board.strapping:
                    out.append(_r("pin.strapping", VStatus.WARNING, f"{board.pin_label(n)} is a strapping pin; may affect boot.", "Prefer a non-strapping pin."))
                owner = used.get(n)
                if owner and owner != con.id:
                    out.append(_r("pin.conflict", VStatus.BLOCKED, f"{board.pin_label(n)} is used by two connections.", "Assign distinct pins."))
                used[n] = con.id
    b = d.behavior
    if b.led_pin not in board.output_pins:
        out.append(_r("pin.led_output", VStatus.BLOCKED, f"{board.pin_label(b.led_pin)} cannot drive an output on {board.name}.", f"Use one of {sorted(board.output_pins)}."))
    else:
        out.append(_r("pin.led_output", VStatus.PASS, f"{board.pin_label(b.led_pin)} supports digital output."))
    if b.sensor_source == "photoresistor_adc":
        if b.sensor_pin is None or b.sensor_pin not in board.adc_pins:
            out.append(_r("pin.analog_input", VStatus.BLOCKED, f"Sensor pin {b.sensor_pin} is not a usable ADC pin on {board.name}.", f"Use one of {sorted(board.adc_pins)}."))
        else:
            out.append(_r("pin.analog_input", VStatus.PASS, f"{board.pin_label(b.sensor_pin)} supports analog input."))
        if b.sensor_pin == b.led_pin:
            out.append(_r("pin.conflict", VStatus.BLOCKED, "LED and sensor share a pin.", "Assign distinct pins."))
    return out


def check_power(d: DesignCandidate, board: BoardProfile) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    bc = _board_component(d)
    bad = False
    for con in d.connections:
        pair = (con.src, con.dst)
        has_5v = any(p.upper().endswith((".5V", ".VIN")) for p in pair)
        has_gpio = any(bc and p.startswith(bc.id + ".") and _pin_number(p) is not None for p in pair)
        if has_5v and has_gpio and not board.five_v_tolerant:
            bad = True
            out.append(_r("power.5v_on_3v3_pin", VStatus.BLOCKED, f"5V wired to a GPIO on {board.name} (not 5V tolerant).", "Use the 3V3 rail for sensor dividers."))
    if not bad:
        out.append(_r("power.voltage_compat", VStatus.PASS, f"All signal levels stay within {board.logic_v} V logic."))
    grounded = any(".GND" in (c.src + c.dst).upper() for c in d.connections)
    out.append(
        _r("power.common_ground", VStatus.PASS, "Circuit shares board GND.")
        if grounded
        else _r("power.common_ground", VStatus.BLOCKED, "No connection to board GND.", "Return every load/sensor to a board GND pin.")
    )
    return out


def check_led(d: DesignCandidate, board: BoardProfile) -> list[ValidationResult]:
    led = next((c for c in d.components if c.type == ComponentType.LED), None)
    if led is None:
        return []
    res = [c for c in d.components if c.type == ComponentType.RESISTOR and c.properties.get("role") == "led_limit"]
    if not res:
        return [_r("led.current_limit", VStatus.BLOCKED, "LED has no current-limiting resistor.", "Add a resistor in series with the LED.")]
    r_ohm = res[0].properties.get("resistance_ohm")
    if not isinstance(r_ohm, (int, float)) or r_ohm <= 0:
        return [_r("led.current_limit", VStatus.UNKNOWN, "LED resistor value unknown.", "Confirm the resistance (read colour bands or measure).")]
    vf_raw = led.properties.get("forward_v")
    vf = float(vf_raw) if isinstance(vf_raw, (int, float)) else LED_VF_DEFAULT
    i_ma = max(0.0, (board.logic_v - vf) / r_ohm * 1000.0)
    msg = f"Estimated LED current {i_ma:.1f} mA (Vf={vf} V{' assumed' if vf_raw is None else ''}, R={r_ohm:g} ohm). Estimate, not measured."
    if i_ma > board.gpio_max_ma:
        return [_r("led.current_limit", VStatus.BLOCKED, msg + f" Exceeds {board.gpio_max_ma} mA pin budget.", "Use a larger resistor.", True)]
    if i_ma < LED_MIN_USEFUL_MA:
        return [_r("led.current_limit", VStatus.WARNING, msg + " LED may be very dim.", "Use a smaller resistor.", True)]
    if vf_raw is None:
        return [_r("led.current_limit", VStatus.WARNING, msg, "Confirm LED colour/forward voltage.", True)]
    return [_r("led.current_limit", VStatus.PASS, msg, estimated=True)]


def check_divider(d: DesignCandidate, board: BoardProfile) -> list[ValidationResult]:
    if d.behavior.sensor_source != "photoresistor_adc":
        return []
    div = [c for c in d.components if c.type == ComponentType.RESISTOR and c.properties.get("role") == "divider"]
    if not div:
        return [_r("sensor.divider", VStatus.BLOCKED, "Photoresistor needs a fixed divider resistor.", "Add ~10k resistor between ADC node and GND.")]
    r = div[0].properties.get("resistance_ohm")
    if not isinstance(r, (int, float)):
        return [_r("sensor.divider", VStatus.UNKNOWN, "Divider resistor value unknown.", "Confirm resistance.")]
    i_ma = board.logic_v / (r + 1000.0) * 1000.0
    return [_r("sensor.divider", VStatus.PASS, f"Divider draws <= {i_ma:.2f} mA (estimate); ADC input stays in 0-{board.logic_v} V.", estimated=True)]


def check_unconfirmed_connections(d: DesignCandidate) -> list[ValidationResult]:
    bad = [c for c in d.connections if c.status == Provenance.UNCERTAIN]
    if bad:
        return [_r("connections.unconfirmed", VStatus.WARNING, f"{len(bad)} connection(s) are unconfirmed.", "Confirm them in assembly.")]
    return []


def validate_design(d: DesignCandidate, inventory: list[ComponentInstance]) -> list[ValidationResult]:
    try:
        board = get_board(d.board)
    except KeyError as e:
        return [_r("board.supported", VStatus.BLOCKED, str(e), "Select a supported board profile.")]
    results: list[ValidationResult] = []
    results += check_required_components(d, inventory)
    results += check_pins(d, board)
    results += check_power(d, board)
    results += check_led(d, board)
    results += check_divider(d, board)
    results += check_unconfirmed_connections(d)
    return results


def execution_allowed(d: DesignCandidate) -> tuple[bool, str]:
    """Hard gate for physical execution: no BLOCKED/UNKNOWN results."""
    if not d.validation:
        return False, "Design has not been validated."
    for v in d.validation:
        if v.status in (VStatus.BLOCKED, VStatus.UNKNOWN):
            return False, f"{v.rule}: {v.message}"
    return True, "ok"
