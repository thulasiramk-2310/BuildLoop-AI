"""Engineering-agent control loop view.

One agent, one state machine (`workflow.TRANSITIONS`). Given the current physical + logical state it decides which tool/
action comes next, why, what needs the human, and when to replan. It is deterministic policy around the model calls:
Muse Spark proposes designs, independent validators judge them, and hardware stays behind explicit approval.
Nothing here executes anything; it only explains and recommends.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .models import ComponentType, Provenance, Stage, VerificationLevel, VStatus
from .workflow import TRANSITIONS, Engine


class NextAction(BaseModel):
    kind: str  # scan | confirm_inventory | plan | approve_design | confirm_step | connect_hardware | authorize | run_test | optical_check | replan | review | done
    label: str
    reason: str
    tab: str | None = None  # UI panel that hosts the action
    needs_human: bool = True


class AgentView(BaseModel):
    stage: Stage
    allowed_next: list[Stage]
    next_action: NextAction
    pending_approvals: list[str] = []
    blockers: list[str] = []
    replan_triggers: list[str] = []
    tools_available: list[str] = []
    evidence_level: str | None = None
    summary: str = ""


TOOLS = ["scan (SAM 3.1)", "plan (Muse Spark, closed tool set)", "validate (deterministic)", "assemble (guided)", "authorize (human)", "test (ESP32 telemetry)", "optical check (camera)", "compare", "redesign"]


def _best_design(designs: list[Any], invalid: dict[str, list[str]]) -> Any | None:
    ok = [d for d in designs if d.id not in invalid and d.overall not in (VStatus.BLOCKED, VStatus.UNKNOWN)]
    if not ok:
        return None
    # Prefer fully passing, then self-contained (no host computer needed).
    ok.sort(key=lambda d: (d.overall != VStatus.PASS, d.behavior.sensor_source != "photoresistor_adc"))
    return ok[0]


def agent_view(engine: Engine, pid: str) -> AgentView:
    p = engine.project(pid)
    st = engine.state(pid)
    comps = [c for c in engine.components(pid) if c.available]
    designs = engine.designs(pid)
    hw = engine.hw
    active = next((d for d in designs if d.id == st.active_design_id), None)
    ver = st.verifications.get(active.id) if active else None

    blockers: list[str] = []
    pending: list[str] = []
    triggers: list[str] = []
    manual = [r for r in engine.revisions(pid) if r.kind == "inventory_change" and r.data.get("manual_override")]
    override_note = f" [manual override of '{manual[-1].data.get('component')}': not camera-observed; rescan to confirm]" if manual else ""

    unconfirmed = [c for c in comps if c.status in (Provenance.DETECTED, Provenance.UNCERTAIN)]
    unknown_vals = [c.name for c in comps if c.type == ComponentType.RESISTOR and c.properties.get("resistance_ohm") is None]
    if unconfirmed:
        pending.append(f"Confirm {len(unconfirmed)} component(s): {', '.join(c.name for c in unconfirmed[:4])}{'…' if len(unconfirmed) > 4 else ''}")
    if unknown_vals:
        blockers.append(f"Unknown resistance: {', '.join(unknown_vals)} (read colour bands or measure)")

    for did, reasons in st.invalidated.items():
        d = next((x for x in designs if x.id == did), None)
        triggers.append(f"'{d.title if d else did}' invalidated: {'; '.join(reasons)}{override_note}")
    if active and active.overall == VStatus.BLOCKED and active.id not in st.invalidated:
        triggers.append(f"Active design '{active.title}' fails validation")
    if ver is not None and ver.passed is False:
        triggers.append(f"Verification contradicted the design: {'; '.join(ver.contradictions[:2])}")
    if p.stage == Stage.TEST and hw and hw.status == "lost":
        triggers.append("Hardware connection lost during test")

    steps = st.steps_by_design.get(active.id, []) if active else []
    next_step = next((s for s in steps if not s.confirmed), None)
    if next_step:
        pending.append(f"Assembly step {next_step.index}: {next_step.title}")
    if active and steps and not next_step and not st.approved_for_execution:
        pending.append("Authorize physical execution")

    # ---- policy -------------------------------------------------------------------------------------
    if not comps and p.stage in (Stage.OBSERVE, Stage.INTERPRET):
        act = NextAction(kind="scan", label="Scan the bench", reason="No inventory yet. The planner only uses parts that are observed or added.", tab=None)
    elif p.stage == Stage.REDESIGN or (triggers and p.stage in (Stage.COMPARE, Stage.TEST)):
        why = triggers[0] if triggers else "Constraints changed."
        act = NextAction(kind="replan", label="Propose a redesign", reason=why + " Re-plan with the current inventory, then review and approve.", tab="copilot")
    elif unconfirmed or unknown_vals:
        act = NextAction(kind="confirm_inventory", label="Confirm components and values", reason="Detections are predictions; resistor values cannot be seen reliably. Confirmed facts keep validation honest.", tab=None)
    elif not designs:
        act = NextAction(kind="plan", label="Design with Muse Spark", reason="Inventory is confirmed. Proposals will be validated by deterministic rules before you see them.", tab="copilot")
    elif not active:
        best = _best_design(designs, st.invalidated)
        if best:
            act = NextAction(kind="approve_design", label=f"Approve '{best.title}'", reason=f"Validation: {best.overall.value}. {best.tradeoffs[0] if best.tradeoffs else ''}".strip(), tab="design")
        else:
            blockers.append("No candidate passes validation")
            act = NextAction(kind="replan", label="Fix blockers, then re-plan", reason="Every candidate is BLOCKED or UNKNOWN. Resolve the listed rules (inventory/values) and plan again.", tab="design")
    elif next_step:
        act = NextAction(kind="confirm_step", label=f"Step {next_step.index}: {next_step.title}", reason="Wiring cannot be observed. Your confirmation is the only record.", tab="assembly")
    elif not (hw and hw.status == "connected"):
        act = NextAction(kind="connect_hardware", label="Connect the board", reason="Select the port yourself and approve opening it. Opening a port can reset the board.", tab="hardware")
    elif not st.approved_for_execution:
        act = NextAction(kind="authorize", label="Authorize execution", reason="All steps are confirmed and validation allows execution. Actuation needs your explicit approval.", tab="hardware")
    elif ver is None:
        act = NextAction(kind="run_test", label="Run the test", reason="Collect sensor/GPIO evidence; results will be graded honestly.", tab="hardware")
    elif ver.level in (VerificationLevel.SENSOR_OBSERVED, VerificationLevel.GPIO_REPORTED, VerificationLevel.COMMAND_ACKED) and ver.passed is not False:
        act = NextAction(kind="optical_check", label="Add independent optical evidence", reason="Reported GPIO/sensor states are not proof the LED lit. Box the LED on camera and run the optical check.", tab="hardware")
    else:
        act = NextAction(kind="review", label="Compare and decide", reason="Evidence recorded. Remove or change a part to watch the design adapt, or finish.", tab="design", needs_human=True)

    level = ver.level.value if ver else None
    summary = f"{p.stage.value}: {act.label}. " + (f"{len(blockers)} blocker(s). " if blockers else "") + (f"{len(triggers)} replan trigger(s)." if triggers else "")
    return AgentView(
        stage=p.stage,
        allowed_next=sorted(TRANSITIONS[p.stage], key=lambda s: s.value),
        next_action=act,
        pending_approvals=pending,
        blockers=blockers,
        replan_triggers=triggers,
        tools_available=TOOLS,
        evidence_level=level,
        summary=summary.strip(),
    )
