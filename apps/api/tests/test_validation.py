
from buildloop.designs import build_adaptive_light, build_phone_sensor_light
from buildloop.models import ComponentType, Connection, VStatus
from buildloop.validation import execution_allowed, validate_design


def status(d, rule):
    return [v.status for v in d.validation if v.rule == rule]


def test_adaptive_light_passes(inventory):
    d = build_adaptive_light(inventory)
    assert d.overall in (VStatus.PASS, VStatus.WARNING)
    assert VStatus.BLOCKED not in {v.status for v in d.validation}
    assert execution_allowed(d)[0]
    assert d.behavior.led_pin == 25 and d.behavior.sensor_pin == 34


def test_missing_photoresistor_blocks(inventory):
    inv = [c for c in inventory if c.type != ComponentType.PHOTORESISTOR]
    d = build_adaptive_light(inv)
    assert VStatus.BLOCKED in status(d, "inventory.required_components")
    assert not execution_allowed(d)[0]


def test_missing_led_resistor_blocks(inventory):
    inv = [c for c in inventory if c.name not in ("r220", "r10k")]
    d = build_phone_sensor_light(inv)
    assert VStatus.BLOCKED in {v.status for v in d.validation}


def test_unknown_resistance_is_unknown_not_pass(inventory):
    for c in inventory:
        if c.name == "r220":
            c.properties["resistance_ohm"] = None
    d = build_adaptive_light(inventory)
    assert VStatus.UNKNOWN in status(d, "led.current_limit")
    assert not execution_allowed(d)[0]


def test_led_current_too_high_blocked(inventory):
    for c in inventory:
        if c.name == "r220":
            c.properties["resistance_ohm"] = 47  # (3.3-2)/47 = 27 mA > 12 mA budget
    d = build_adaptive_light(inventory)
    assert VStatus.BLOCKED in status(d, "led.current_limit")
    assert any(v.estimated for v in d.validation if v.rule == "led.current_limit")


def test_adc_pin_must_be_adc1(inventory):
    d = build_adaptive_light(inventory)
    d.behavior.sensor_pin = 4  # ADC2 pin: unusable with WiFi, not in adc_pins
    d.validation = validate_design(d, inventory)
    assert VStatus.BLOCKED in status(d, "pin.analog_input")


def test_input_only_pin_cannot_drive_led(inventory):
    d = build_adaptive_light(inventory)
    d.behavior.led_pin = 34
    d.validation = validate_design(d, inventory)
    assert VStatus.BLOCKED in status(d, "pin.led_output")


def test_five_volt_to_gpio_blocked_on_esp32(inventory):
    d = build_adaptive_light(inventory)
    bid = d.components[0].id
    d.connections.append(Connection(src=f"{bid}.5V", dst=f"{bid}.GPIO32"))
    d.validation = validate_design(d, inventory)
    assert VStatus.BLOCKED in status(d, "power.5v_on_3v3_pin")


def test_pin_conflict_detected(inventory):
    d = build_adaptive_light(inventory)
    bid = d.components[0].id
    d.connections.append(Connection(src=f"{bid}.GPIO25", dst="x.a"))
    d.validation = validate_design(d, inventory)
    assert VStatus.BLOCKED in status(d, "pin.conflict")


def test_no_ground_blocked(inventory):
    d = build_phone_sensor_light(inventory)
    d.connections = [c for c in d.connections if "GND" not in c.dst]
    d.validation = validate_design(d, inventory)
    assert VStatus.BLOCKED in status(d, "power.common_ground")


def test_unconfirmed_components_warn(inventory):
    from buildloop.models import Provenance

    inventory[1].status = Provenance.DETECTED
    d = build_adaptive_light(inventory)
    assert VStatus.WARNING in status(d, "inventory.unconfirmed")


def test_unsupported_board_blocked(inventory):
    d = build_adaptive_light(inventory)
    d.board = "mystery-board"
    assert validate_design(d, inventory)[0].status == VStatus.BLOCKED


def test_strapping_pin_warns(inventory):
    d = build_adaptive_light(inventory)
    bid = d.components[0].id
    d.connections.append(Connection(src=f"{bid}.GPIO12", dst="x.a"))
    d.validation = validate_design(d, inventory)
    assert VStatus.WARNING in status(d, "pin.strapping")
