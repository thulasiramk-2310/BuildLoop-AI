"""Reality-synced digital twin: snapshots, inventory diffs, design invalidation."""
from __future__ import annotations

from .models import (
    ComponentInstance,
    DesignCandidate,
    DigitalTwinSnapshot,
    InventoryDiff,
    ValidationResult,
    VStatus,
)
from .validation import check_required_components


def snapshot(revision: int, components: list[ComponentInstance]) -> DigitalTwinSnapshot:
    return DigitalTwinSnapshot(revision=revision, components=[c.model_copy(deep=True) for c in components])


def diff_snapshots(prev: DigitalTwinSnapshot | None, curr: DigitalTwinSnapshot) -> InventoryDiff:
    """Availability-aware diff: a component that is present but marked unavailable counts as removed."""
    prev_map = {c.id: c for c in (prev.components if prev else []) if c.available}
    curr_map = {c.id: c for c in curr.components if c.available}
    added = [c for i, c in curr_map.items() if i not in prev_map]
    removed = [c for i, c in prev_map.items() if i not in curr_map]
    changed = [
        i
        for i, c in curr_map.items()
        if i in prev_map and (prev_map[i].properties != c.properties or prev_map[i].type != c.type)
    ]
    return InventoryDiff(added=added, removed=removed, changed=changed)


def invalidate_designs(
    designs: list[DesignCandidate], inventory: list[ComponentInstance]
) -> list[tuple[DesignCandidate, list[ValidationResult]]]:
    """Return designs whose hardware requirements are no longer satisfied, with the blocking results."""
    out = []
    for d in designs:
        blocked = [r for r in check_required_components(d, inventory) if r.status == VStatus.BLOCKED]
        if blocked:
            out.append((d, blocked))
    return out
