"""Expected-vs-observed evaluation with honest evidence levels.

Levels never exceed what the evidence supports. Simulated telemetry caps at SENSOR_OBSERVED and is flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .models import (
    DesignCandidate,
    Evidence,
    TelemetryRecord,
    VerificationLevel,
    VerificationResult,
)
from .optical import OpticalResult

MIN_ADC_SWING = 500  # counts between bright and dark phases to call the sensor "responding"
MIN_OPTICAL_DELTA = 0.05  # 0-1 luminance rise in LED region when LED is on


@dataclass
class Phase:
    label: str  # "bright" | "dark"
    samples: list[TelemetryRecord]
    expected_led: bool
    optical_roi: list[float] = field(default_factory=list)  # independent phone-camera luminance of LED region


def _led_expected(adc: float, threshold: float, dark_means: str) -> bool:
    return adc < threshold if dark_means == "adc_low" else adc > threshold


def evaluate_photoresistor(design: DesignCandidate, phases: list[Phase], acks: list[str]) -> VerificationResult:
    b = design.behavior
    ev: list[Evidence] = []
    contra: list[str] = []
    simulated = any(s.source == "simulated" for p in phases for s in p.samples)
    level = VerificationLevel.NONE

    if acks:
        ev.append(Evidence(kind="command_ack", detail=f"Device acknowledged: {', '.join(acks)}", level=VerificationLevel.COMMAND_ACKED, simulated=simulated))
        level = VerificationLevel.COMMAND_ACKED

    medians: dict[str, float] = {}
    reported_ok = True
    for p in phases:
        adcs = [s.adc for s in p.samples if s.adc is not None]
        leds = [s.led_state for s in p.samples if s.led_state is not None]
        if not adcs or not leds:
            ev.append(Evidence(kind="no_data", detail=f"No telemetry in '{p.label}' phase", level=VerificationLevel.NONE, simulated=simulated))
            reported_ok = False
            continue
        m = median(adcs)
        medians[p.label] = m
        led_on = sum(1 for x in leds if x) > len(leds) / 2
        ev.append(Evidence(kind="gpio_state", detail=f"{p.label}: median ADC {m:.0f}, LED pin reported {'ON' if led_on else 'OFF'} (n={len(adcs)})", level=VerificationLevel.GPIO_REPORTED, simulated=simulated))
        if led_on != p.expected_led:
            reported_ok = False
            contra.append(f"{p.label}: expected LED {'ON' if p.expected_led else 'OFF'} but device reported {'ON' if led_on else 'OFF'}")
        rule_says = _led_expected(m, b.threshold, b.dark_means)
        if rule_says != led_on:
            contra.append(f"{p.label}: ADC {m:.0f} vs threshold {b.threshold:.0f} implies LED {'ON' if rule_says else 'OFF'} but device reported {'ON' if led_on else 'OFF'}")
            reported_ok = False
    if reported_ok and phases and all(p.label in medians for p in phases):
        level = max(level, VerificationLevel.GPIO_REPORTED, key=_rank)

    swing_ok = False
    if "bright" in medians and "dark" in medians:
        swing = abs(medians["bright"] - medians["dark"])
        swing_ok = swing >= MIN_ADC_SWING
        ev.append(Evidence(kind="sensor_response", detail=f"ADC swing bright->dark = {swing:.0f} counts ({'responding' if swing_ok else 'too small'})", level=VerificationLevel.SENSOR_OBSERVED, simulated=simulated))
        if swing_ok and reported_ok:
            level = max(level, VerificationLevel.SENSOR_OBSERVED, key=_rank)

    optical_ok = None
    on_roi = [x for p in phases if p.expected_led for x in p.optical_roi]
    off_roi = [x for p in phases if not p.expected_led for x in p.optical_roi]
    if on_roi and off_roi:
        delta = median(on_roi) - median(off_roi)
        optical_ok = delta >= MIN_OPTICAL_DELTA
        ev.append(Evidence(kind="optical", detail=f"Phone camera LED-region luminance rose by {delta:.3f} when LED on", level=VerificationLevel.OPTICAL_OBSERVED, simulated=False))
        if not optical_ok:
            contra.append("Device reports LED on but camera sees no luminance rise in the LED region.")
        elif not contra:
            level = max(level, VerificationLevel.OPTICAL_OBSERVED, key=_rank)

    if simulated:
        level = min(level, VerificationLevel.SENSOR_OBSERVED, key=_rank)
    if optical_ok and not simulated and swing_ok and not contra:
        level = VerificationLevel.PHYSICAL_VERIFIED

    passed: bool | None
    if contra:
        passed = False if not (optical_ok is False) else None
    elif _rank(level) >= _rank(VerificationLevel.SENSOR_OBSERVED):
        passed = True
    else:
        passed = None
    tag = " SIMULATED device: this is not physical evidence." if simulated else ""
    if contra:
        summary = "Contradictory evidence: " + "; ".join(contra) + tag
    elif passed:
        summary = f"Behavior consistent with design at level '{level.value}'." + tag
    else:
        summary = "Inconclusive: not enough evidence." + tag
    return VerificationResult(design_id=design.id, passed=passed, level=level, evidence=ev, contradictions=contra, summary=summary)


_ORDER = [
    VerificationLevel.NONE,
    VerificationLevel.COMMAND_ACKED,
    VerificationLevel.GPIO_REPORTED,
    VerificationLevel.SENSOR_OBSERVED,
    VerificationLevel.OPTICAL_OBSERVED,
    VerificationLevel.PHYSICAL_VERIFIED,
]


def _rank(v: VerificationLevel) -> int:
    return _ORDER.index(v)


@dataclass
class PhoneSample:
    brightness: float  # calibrated 0-1
    commanded_led: bool | None  # what backend commanded after this sample
    led_reported: bool | None  # device-reported LED pin state after command
    acked: bool


def evaluate_phone_light(design: DesignCandidate, samples: list[PhoneSample], simulated: bool) -> VerificationResult:
    b = design.behavior
    ev: list[Evidence] = []
    contra: list[str] = []
    if not samples:
        return VerificationResult(design_id=design.id, passed=None, level=VerificationLevel.NONE, evidence=[], summary="No samples.")
    acked = [s for s in samples if s.acked]
    level = VerificationLevel.NONE
    if acked:
        level = VerificationLevel.COMMAND_ACKED
        ev.append(Evidence(kind="command_ack", detail=f"{len(acked)} LED commands acknowledged", level=level, simulated=simulated))
    wrong = 0
    for s in samples:
        want = s.brightness < b.threshold
        if s.commanded_led is not None and s.commanded_led != want:
            wrong += 1
            contra.append(f"brightness {s.brightness:.2f} should give LED {'ON' if want else 'OFF'}, commanded {'ON' if s.commanded_led else 'OFF'}")
        if s.led_reported is not None and s.commanded_led is not None and s.led_reported != s.commanded_led:
            contra.append("device-reported LED state differs from the command")
    reported = [s for s in samples if s.led_reported is not None]
    if reported and not contra:
        level = VerificationLevel.GPIO_REPORTED
        ev.append(Evidence(kind="gpio_state", detail=f"Device reported LED pin state for {len(reported)} commands, all matching", level=level, simulated=simulated))
    bright = [s for s in samples if s.brightness >= b.threshold]
    dark = [s for s in samples if s.brightness < b.threshold]
    if bright and dark and not contra and level == VerificationLevel.GPIO_REPORTED:
        ev.append(Evidence(kind="phone_sensor", detail=f"Phone brightness crossed threshold {b.threshold:.2f} both ways ({len(bright)} bright, {len(dark)} dark samples); LED followed.", level=VerificationLevel.SENSOR_OBSERVED, simulated=simulated))
        level = VerificationLevel.SENSOR_OBSERVED
    passed = False if contra else (True if _rank(level) >= _rank(VerificationLevel.SENSOR_OBSERVED) else None)
    tag = " SIMULATED device: not physical evidence." if simulated else " LED illumination not independently observed."
    summary = ("Contradictory evidence: " + "; ".join(contra[:3]) if contra else f"Phone-driven control consistent at level '{level.value}'.") + tag
    return VerificationResult(design_id=design.id, passed=passed, level=level, evidence=ev, contradictions=contra, summary=summary)


def apply_optical(prior: VerificationResult | None, design: DesignCandidate, optical: OpticalResult, simulated: bool, acked: bool) -> VerificationResult:
    """Merge independent camera evidence into a verification result without overstating it.

    Optical evidence lifts the level only for a real (non-simulated) device with no contradictions. A camera
    watching a simulated LED proves nothing about hardware, so it is recorded but capped.
    """
    ev = list(prior.evidence) if prior else []
    contra = list(prior.contradictions) if prior else []
    level = prior.level if prior else (VerificationLevel.COMMAND_ACKED if acked else VerificationLevel.NONE)
    passed = prior.passed if prior else None
    if optical.outcome == "observed":
        ev.append(Evidence(kind="optical", detail=optical.summary, level=VerificationLevel.OPTICAL_OBSERVED, simulated=simulated))
        if not simulated and not contra:
            level = max(level, VerificationLevel.OPTICAL_OBSERVED, key=_rank)
            # PHYSICAL_VERIFIED needs: real device + sensor-level evidence that already passed + visible LED change.
            if prior and prior.passed and _rank(prior.level) >= _rank(VerificationLevel.SENSOR_OBSERVED):
                level = VerificationLevel.PHYSICAL_VERIFIED
            passed = True if (passed is None or passed) else passed
    elif optical.outcome == "not_observed":
        ev.append(Evidence(kind="optical", detail=optical.summary, level=VerificationLevel.NONE, simulated=simulated))
        contra.append("Device reported/commanded LED ON but the camera saw no visible brightness change.")
        passed = None
    else:
        ev.append(Evidence(kind="optical_uncertain", detail=optical.summary, level=VerificationLevel.NONE, simulated=simulated))
    tag = " SIMULATED device: not physical evidence." if simulated else ""
    if contra:
        summary = "Contradictory evidence: " + "; ".join(contra) + tag
    else:
        summary = f"Level '{level.value}'. " + optical.summary + tag
    return VerificationResult(design_id=design.id, passed=passed, level=level, evidence=ev, contradictions=contra, summary=summary)
