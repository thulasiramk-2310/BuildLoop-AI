"""Optical LED verification from camera luminance of a user-selected region.

The evidence is *visible brightness change in a region*. It does not prove electrical correctness or safety, and it is
reported as uncertain when ambient light drifts, auto-exposure moves, or the baseline is noisy.
"""
from __future__ import annotations

from statistics import median, pstdev

from pydantic import BaseModel

MIN_SAMPLES_PER_PHASE = 5
MIN_DELTA = 0.05  # median luma rise (0-1) required between OFF and ON
MAX_BASELINE_NOISE = 0.02  # population stddev of OFF samples above this => unreliable
MAX_DRIFT = 0.03  # |median(OFF-before) - median(OFF-after)| above this => ambient drift
NOISE_MULT = 3.0  # delta must exceed NOISE_MULT x noise


class OpticalSample(BaseModel):
    t: float  # seconds since test start
    luma: float  # mean luma of the ROI, 0-1
    phase: str  # "off_before" | "on" | "off_after"


class OpticalResult(BaseModel):
    outcome: str  # "observed" | "not_observed" | "uncertain"
    delta: float | None = None
    baseline: float | None = None
    noise: float | None = None
    reasons: list[str] = []
    summary: str = ""


def evaluate_optical(samples: list[OpticalSample]) -> OpticalResult:
    ph = {k: [s.luma for s in samples if s.phase == k] for k in ("off_before", "on", "off_after")}
    reasons: list[str] = []
    for k, v in ph.items():
        if len(v) < MIN_SAMPLES_PER_PHASE:
            reasons.append(f"Only {len(v)} samples in phase '{k}' (need {MIN_SAMPLES_PER_PHASE}).")
    if reasons:
        return OpticalResult(outcome="uncertain", reasons=reasons, summary="Not enough samples to judge. " + " ".join(reasons))

    off_before, on, off_after = (median(ph[k]) for k in ("off_before", "on", "off_after"))
    baseline = (off_before + off_after) / 2
    noise = max(pstdev(ph["off_before"]), pstdev(ph["off_after"]), pstdev(ph["on"]))
    delta = on - baseline
    drift = abs(off_before - off_after)

    if noise > MAX_BASELINE_NOISE:
        reasons.append(f"Baseline noise {noise:.3f} is high (moving camera, flicker or exposure changes).")
    if drift > MAX_DRIFT:
        reasons.append(f"Ambient/exposure drift {drift:.3f} between the two OFF phases.")
    if reasons:
        return OpticalResult(outcome="uncertain", delta=delta, baseline=baseline, noise=noise, reasons=reasons, summary="Optical result is uncertain: " + " ".join(reasons) + " Lock exposure, hold the camera still and retry.")

    if delta >= max(MIN_DELTA, NOISE_MULT * noise):
        return OpticalResult(outcome="observed", delta=delta, baseline=baseline, noise=noise, summary=f"Region brightened by {delta:.3f} (baseline {baseline:.3f}, noise {noise:.3f}) while the LED was commanded ON and returned to baseline after. Visible change only; not proof of electrical correctness.")
    return OpticalResult(outcome="not_observed", delta=delta, baseline=baseline, noise=noise, summary=f"No visible brightness change in the selected region (delta {delta:.3f}). The LED may be off, mis-wired, outside the region, or too dim for this camera.")
