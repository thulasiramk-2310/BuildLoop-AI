"""Provider interfaces, shared errors, usage metering."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from ..models import ComponentInstance, DesignCandidate


class ProviderError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


@dataclass
class Detection:
    concept: str
    box: tuple[float, float, float, float]  # x1,y1,x2,y2 in original image pixels (inclusive)
    img_w: int
    img_h: int
    confidence: float | None = None
    mask_path: str | None = None  # SVG path in mask-local coordinates
    mask_bounds: tuple[float, float, float, float] | None = None  # placement in source image


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    budget: int = 200

    def check(self) -> None:
        if self.calls >= self.budget:
            raise ProviderError("budget_exhausted", f"Request budget of {self.budget} reached.", "Raise META_MAX_REQUESTS or restart.")

    def record(self, started: float) -> None:
        self.calls += 1
        self.latencies_ms.append((time.monotonic() - started) * 1000)

    def as_dict(self) -> dict:
        lat = self.latencies_ms
        return {"calls": self.calls, "cache_hits": self.cache_hits, "errors": self.errors, "budget": self.budget, "avg_latency_ms": (sum(lat) / len(lat)) if lat else None}


class SegmentationProvider(Protocol):
    name: str
    live: bool  # True only for real Meta API calls

    async def segment(self, image: bytes, concept: str) -> list[Detection]: ...

    def usage(self) -> dict: ...


@dataclass
class PlanContext:
    goal: str
    board_id: str
    inventory: list[ComponentInstance]
    prior_designs: list[DesignCandidate] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)  # validation failures / missing components
    telemetry_summary: str = ""


class ReasoningProvider(Protocol):
    name: str
    live: bool

    async def propose_designs(self, ctx: PlanContext) -> list[DesignCandidate]: ...

    async def interpret_scene(self, image: bytes, detections: list[Detection]) -> list[ComponentInstance]: ...

    def usage(self) -> dict: ...
