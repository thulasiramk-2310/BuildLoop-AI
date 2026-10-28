"""Measured observations only. Every record carries provenance so reviewers can tell live from simulated/recorded."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import Store
from .models import new_id, now

Provenance = Literal["live", "simulated", "recorded"]


class Metric(BaseModel):
    id: str = Field(default_factory=lambda: new_id("met"))
    name: str  # e.g. sam.latency_ms, muse.latency_ms, serial.latency_ms, redesign.seconds
    value: float
    unit: str = "ms"
    provenance: Provenance
    at: datetime = Field(default_factory=now)
    meta: dict[str, Any] = {}


class Metrics:
    GLOBAL = "global"

    def __init__(self, store: Store | None = None):
        self.store = store
        self._mem: list[Metric] = []

    def record(self, name: str, value: float, unit: str = "ms", provenance: Provenance = "live", **meta: Any) -> Metric:
        m = Metric(name=name, value=float(value), unit=unit, provenance=provenance, meta=meta)
        self._mem.append(m)
        if self.store:
            self.store.put(self.GLOBAL, "metric", m.id, m)
        return m

    def all(self) -> list[Metric]:
        if self.store:
            return self.store.list(self.GLOBAL, "metric", Metric)
        return list(self._mem)

    def summary(self) -> dict[str, Any]:
        groups: dict[tuple[str, str], list[float]] = defaultdict(list)
        units: dict[str, str] = {}
        for m in self.all():
            groups[(m.name, m.provenance)].append(m.value)
            units[m.name] = m.unit
        out: dict[str, Any] = {}
        for (name, prov), vals in sorted(groups.items()):
            out.setdefault(name, {"unit": units[name], "by_provenance": {}})["by_provenance"][prov] = {
                "count": len(vals),
                "mean": float(f"{mean(vals):.6g}"),
                "median": float(f"{median(vals):.6g}"),
                "min": float(f"{min(vals):.6g}"),
                "max": float(f"{max(vals):.6g}"),
            }
        return out
