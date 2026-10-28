"""Provider factory. Demo providers are never returned for Real Mode."""
from __future__ import annotations

from ..config import Settings
from ..models import Mode
from .demo import DemoReasoning, DemoSegmentation
from .meta import MetaReasoning, MetaSegmentation
from .open_weight import OpenWeightReasoning, VlmPerception


def make_provider_factory(settings: Settings, metrics=None):
    demo = (DemoSegmentation(), DemoReasoning())
    # SAM 3.1 always goes through the Meta Model API. Only the reasoning backend is switchable; the choice is explicit
    # configuration, never an automatic fallback, and the chosen backend is labelled in every proposal.
    reasoning = OpenWeightReasoning(settings, metrics=metrics) if settings.REASONING_BACKEND == "open_weight" else MetaReasoning(settings, metrics=metrics)
    if settings.PERCEPTION_BACKEND == "vlm":
        ow = reasoning if isinstance(reasoning, OpenWeightReasoning) else OpenWeightReasoning(settings, metrics=metrics)
        perception = VlmPerception(ow)
    else:
        perception = MetaSegmentation(settings, metrics=metrics)
    live = (perception, reasoning)

    def for_mode(mode: Mode):
        return demo if mode == Mode.DEMO else live

    return for_mode
