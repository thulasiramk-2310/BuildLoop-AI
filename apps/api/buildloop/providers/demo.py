"""Demo (recorded/synthetic) providers. Always labelled live=False; never used in Real Mode."""
from __future__ import annotations

from ..designs import alternatives_for_missing, build_adaptive_light, build_phone_sensor_light, default_pins
from ..models import BBox, ComponentInstance, ComponentType, DesignCandidate, Provenance
from .base import Detection, PlanContext

W, H = 1280, 720

# (concept, type, name, box, confidence, properties)
_SCENE = [
    ("esp32 development board", ComponentType.ESP32, "ESP32 DevKitC", (80, 120, 360, 560), 0.93, {}),
    ("breadboard", ComponentType.BREADBOARD, "Breadboard", (420, 100, 1180, 600), 0.9, {}),
    ("led", ComponentType.LED, "Red LED", (560, 180, 610, 260), 0.84, {"forward_v": None}),
    ("resistor", ComponentType.RESISTOR, "Resistor A", (650, 300, 760, 340), 0.77, {"resistance_ohm": None}),
    ("resistor", ComponentType.RESISTOR, "Resistor B", (800, 300, 910, 340), 0.74, {"resistance_ohm": None}),
    ("photoresistor", ComponentType.PHOTORESISTOR, "Photoresistor", (960, 190, 1030, 260), 0.71, {}),
]


def demo_detections() -> list[Detection]:
    return [Detection(concept=c, box=b, img_w=W, img_h=H, confidence=conf) for c, _t, _n, b, conf, _p in _SCENE]


def demo_components() -> list[ComponentInstance]:
    out = []
    for i, (_c, t, n, b, conf, props) in enumerate(_SCENE):
        out.append(
            ComponentInstance(
                id=f"demo_{i}_{t.value}",
                type=t,
                name=n,
                bbox=BBox(x1=b[0], y1=b[1], x2=b[2], y2=b[3], img_w=W, img_h=H),
                pins=default_pins(t),
                properties=dict(props),
                confidence=conf,
                status=Provenance.DETECTED if conf >= 0.75 else Provenance.UNCERTAIN,
            )
        )
    return out


class DemoSegmentation:
    name = "demo-fixture"
    live = False

    def __init__(self) -> None:
        self._calls = 0

    async def segment(self, image: bytes, concept: str) -> list[Detection]:
        self._calls += 1
        c = concept.lower()
        return [d for d in demo_detections() if c in d.concept or d.concept in c]

    def usage(self) -> dict:
        return {"calls": self._calls, "cache_hits": 0, "errors": 0, "budget": None, "avg_latency_ms": None}


class DemoReasoning:
    """Deterministic planner standing in for Muse Spark. Proposals still go through the validator."""

    name = "demo-fixture"
    live = False

    def __init__(self) -> None:
        self._calls = 0

    async def propose_designs(self, ctx: PlanContext) -> list[DesignCandidate]:
        self._calls += 1
        has_ldr = any(c.available and c.type == ComponentType.PHOTORESISTOR for c in ctx.inventory)
        if has_ldr:
            out = [build_adaptive_light(ctx.inventory, goal=ctx.goal), build_phone_sensor_light(ctx.inventory, goal=ctx.goal)]
        else:
            out = alternatives_for_missing(ctx.inventory, goal=ctx.goal)
        for d in out:
            d.source = "demo-fixture"
        return out

    async def interpret_scene(self, image: bytes, detections: list[Detection]) -> list[ComponentInstance]:
        self._calls += 1
        return demo_components()

    def usage(self) -> dict:
        return {"calls": self._calls, "cache_hits": 0, "errors": 0, "budget": None, "avg_latency_ms": None}
