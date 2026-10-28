"""Engineering state machine + orchestration engine (Observe -> ... -> Verify)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel

from . import twin
from .db import Store
from .designs import generate_assembly_steps
from .hardware.session import HardwareError, HardwareSession
from .imaging import ImageError, preprocess
from .metrics import Metrics
from .models import (
    AssemblyStep,
    ComponentInstance,
    ComponentType,
    DesignCandidate,
    DigitalTwinSnapshot,
    Mode,
    Project,
    Provenance,
    RevisionEvent,
    Stage,
    VerificationResult,
)
from .optical import OpticalSample, evaluate_optical
from .providers.base import PlanContext, ProviderError
from .validation import execution_allowed, validate_design
from .verification import Phase, PhoneSample, apply_optical, evaluate_phone_light, evaluate_photoresistor

TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.OBSERVE: {Stage.INTERPRET, Stage.OBSERVE},
    Stage.INTERPRET: {Stage.CONFIRM, Stage.OBSERVE},
    Stage.CONFIRM: {Stage.PLAN, Stage.OBSERVE},
    Stage.PLAN: {Stage.VALIDATE, Stage.CONFIRM},
    Stage.VALIDATE: {Stage.GUIDE, Stage.PLAN, Stage.REDESIGN},
    Stage.GUIDE: {Stage.TEST, Stage.PLAN, Stage.REDESIGN, Stage.VALIDATE},
    Stage.TEST: {Stage.COMPARE, Stage.GUIDE, Stage.REDESIGN},
    Stage.COMPARE: {Stage.REDESIGN, Stage.COMPLETE, Stage.TEST},
    Stage.REDESIGN: {Stage.VALIDATE, Stage.PLAN, Stage.GUIDE, Stage.CONFIRM},
    Stage.COMPLETE: {Stage.OBSERVE, Stage.REDESIGN},
}


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


class ProjectState(BaseModel):
    """Mutable per-project pointers, persisted as one record."""

    project_id: str
    active_design_id: str | None = None
    original_design_id: str | None = None
    approved_for_execution: bool = False
    revision: int = 0
    steps_by_design: dict[str, list[AssemblyStep]] = {}
    verifications: dict[str, VerificationResult] = {}
    invalidated: dict[str, list[str]] = {}  # design_id -> reasons


class Engine:
    def __init__(self, store: Store, providers: Any, metrics: Metrics | None = None):
        """providers: callable(Mode) -> (SegmentationProvider, ReasoningProvider). Mode picks demo vs live."""
        self.store, self._providers = store, providers
        self.metrics = metrics or Metrics(store)
        self._redesign_started: dict[str, float] = {}
        self._optical_t0: dict[str, float] = {}
        self.hw: HardwareSession | None = None
        self._phase_buf: dict[str, list] = {}
        self._phone_samples: dict[str, list[PhoneSample]] = {}
        self._phone_led: dict[str, bool | None] = {}
        self._optical: dict[str, list[tuple[bool, float]]] = {}
        self.decision_traces: dict[str, list[dict]] = {}

    # ---- helpers -----------------------------------------------------------------------
    def project(self, pid: str) -> Project:
        p = self.store.get(pid, Project)
        if not p:
            raise WorkflowError("project_not_found", f"Project {pid} not found")
        return p

    def state(self, pid: str) -> ProjectState:
        return self.store.get(f"state_{pid}", ProjectState) or ProjectState(project_id=pid)

    def _save_state(self, st: ProjectState) -> None:
        self.store.put(st.project_id, "state", f"state_{st.project_id}", st)

    def components(self, pid: str) -> list[ComponentInstance]:
        return self.store.list(pid, "component", ComponentInstance)

    def designs(self, pid: str) -> list[DesignCandidate]:
        return self.store.list(pid, "design", DesignCandidate)

    def design(self, pid: str, did: str) -> DesignCandidate:
        d = self.store.get(did, DesignCandidate)
        if not d:
            raise WorkflowError("design_not_found", f"Design {did} not found")
        return d

    def revisions(self, pid: str) -> list[RevisionEvent]:
        return self.store.list(pid, "revision", RevisionEvent)

    def log(self, pid: str, kind: str, summary: str, **data: Any) -> RevisionEvent:
        ev = RevisionEvent(project_id=pid, kind=kind, summary=summary, data=data)
        self.store.put(pid, "revision", ev.id, ev)
        return ev

    def set_stage(self, pid: str, to: Stage, *, force: bool = False) -> Project:
        p = self.project(pid)
        if to == p.stage:
            return p
        if not force and to not in TRANSITIONS[p.stage]:
            raise WorkflowError("illegal_transition", f"Cannot go {p.stage.value} -> {to.value}", f"Allowed: {sorted(s.value for s in TRANSITIONS[p.stage])}")
        if to == Stage.TEST:
            self._assert_can_execute(pid)
        if to == Stage.REDESIGN:
            self._redesign_started.setdefault(pid, time.monotonic())
        self.log(pid, "stage", f"{p.stage.value} -> {to.value}")
        p.stage = to
        self.store.put(pid, "project", pid, p)
        return p

    # ---- projects ----------------------------------------------------------------------
    def create_project(self, name: str, goal: str, mode: Mode, board: str) -> Project:
        p = Project(name=name, goal=goal, mode=mode, board=board)
        self.store.put(p.id, "project", p.id, p)
        self._save_state(ProjectState(project_id=p.id))
        self.log(p.id, "decision", f"Project created in {mode.value} mode", goal=goal)
        return p

    def list_projects(self) -> list[Project]:
        return self.store.list_all("project", Project)

    # ---- observe / inventory -----------------------------------------------------------
    def _snapshot(self, pid: str) -> DigitalTwinSnapshot:
        st = self.state(pid)
        st.revision += 1
        snap = twin.snapshot(st.revision, self.components(pid))
        self.store.put(pid, "snapshot", f"snap_{pid}_{st.revision}", snap)
        self._save_state(st)
        return snap

    def snapshots(self, pid: str) -> list[DigitalTwinSnapshot]:
        return self.store.list(pid, "snapshot", DigitalTwinSnapshot)

    def _sync(self, pid: str, reason: str, extra: dict | None = None) -> dict:
        """Snapshot, diff against previous, invalidate dependent designs, force redesign when needed."""
        snaps = self.snapshots(pid)
        prev = snaps[-1] if snaps else None
        cur = self._snapshot(pid)
        diff = twin.diff_snapshots(prev, cur)
        invalidated = []
        if diff.removed or diff.changed:
            comps = self.components(pid)
            st = self.state(pid)
            for d, blocked in twin.invalidate_designs(self.designs(pid), comps):
                reasons = [b.message for b in blocked]
                if d.id not in st.invalidated:
                    st.invalidated[d.id] = reasons
                    invalidated.append(d.id)
                    self.log(pid, "design_invalidated", f"'{d.title}' no longer meets hardware requirements", design_id=d.id, reasons=reasons)
                d.validation = validate_design(d, comps)
                self.store.put(pid, "design", d.id, d)
            self._save_state(st)
        if diff.added or diff.removed or diff.changed:
            self.log(pid, "inventory_change", f"{reason}: +{len(diff.added)} -{len(diff.removed)} ~{len(diff.changed)}", added=[c.name for c in diff.added], removed=[c.name for c in diff.removed], changed=diff.changed, **(extra or {}))
        st = self.state(pid)
        if invalidated and st.active_design_id in invalidated:
            st.approved_for_execution = False
            self._save_state(st)
            self.hw and self.hw.revoke()
            self.set_stage(pid, Stage.REDESIGN, force=True)
        return {"diff": diff.model_dump(mode="json"), "invalidated": invalidated, "revision": cur.revision}

    async def scan(self, pid: str, image: bytes) -> dict:
        p = self.project(pid)
        if p.stage in (Stage.PLAN, Stage.VALIDATE, Stage.GUIDE, Stage.TEST, Stage.COMPARE, Stage.COMPLETE):
            pass  # rescans allowed anytime; they only update inventory
        if p.stage == Stage.OBSERVE:
            self.set_stage(pid, Stage.INTERPRET)
        segmentation, reasoning = self._providers(p.mode)
        frame_report = None
        if segmentation.live:  # demo providers ignore pixels; live providers get a bounded, metadata-free frame
            try:
                segmentation.ensure_ready()  # credentials are checked before any image work
            except ProviderError as e:
                raise WorkflowError(e.code, e.message, e.hint) from e
            try:
                image, rep = preprocess(image)
            except ImageError as e:
                raise WorkflowError("bad_image", str(e), "Retake the photo (JPEG/PNG, at least 160 px).") from e
            frame_report = rep.as_dict()
            self.metrics.record("frame.bytes_sent", rep.bytes_out, "bytes", "live", resized=rep.resized)
            if rep.warnings:
                self.metrics.record("frame.quality_warnings", len(rep.warnings), "count", "live")
        t_scan = time.monotonic()
        prov = "live" if segmentation.live else "simulated"
        dets: list = []
        errors: list[dict] = []
        if hasattr(segmentation, "scan_scene"):
            # Vision-language perception (explicit PERCEPTION_BACKEND=vlm): whole-frame inventory, boxes are model estimates, no masks.
            try:
                comps = await segmentation.scan_scene(image)
            except ProviderError as e:
                raise WorkflowError(e.code, e.message, e.hint or "Check provider credentials/credits, or use Demo Mode.") from e
            self.metrics.record("vlm.scan_total_ms", (time.monotonic() - t_scan) * 1000, "ms", prov)
            self.metrics.record("vlm.scan_components", len(comps), "count", prov)
        else:
            concepts = ["esp32 development board", "breadboard", "led", "resistor", "photoresistor", "pushbutton", "jumper wire"]
            for c in concepts:
                try:
                    dets += await segmentation.segment(image, c)
                except ProviderError as e:
                    errors.append({"concept": c, "code": e.code, "message": e.message})
                    if e.code in ("auth_invalid", "no_api_key", "budget_exhausted", "payment_required"):
                        break
            if errors and not dets:
                raise WorkflowError(errors[0]["code"], errors[0]["message"], "Check provider credentials/credits or use Demo Mode.")
            self.metrics.record("sam.scan_total_ms", (time.monotonic() - t_scan) * 1000, "ms", prov)
            self.metrics.record("sam.scan_detections", len(dets), "count", prov)
            self.metrics.record("sam.concepts_failed", len(errors), "count", prov)
            self.metrics.record("sam.concepts_ok", len(concepts) - len(errors), "count", prov)
            comps = await reasoning.interpret_scene(image, dets)
        for c in comps:  # component ids are global primary keys: namespace per project so scans never collide
            if not c.id.startswith(f"{pid}_"):
                c.id = f"{pid}_{c.id}"
        existing = {c.id: c for c in self.components(pid)}
        for c in comps:
            if c.id in existing:
                c.status = existing[c.id].status if existing[c.id].status == Provenance.USER_CONFIRMED else c.status
            self.store.put(pid, "component", c.id, c)
        res = self._sync(pid, "scan")
        if self.project(pid).stage == Stage.INTERPRET:
            self.set_stage(pid, Stage.CONFIRM)
        return {"detections": [d.__dict__ for d in dets], "components": [c.model_dump(mode="json") for c in comps], "errors": errors, "provider": segmentation.name, "live": segmentation.live, "frame": frame_report, **res}

    def discard_component(self, pid: str, cid: str) -> dict:
        """Remove a FALSE detection entirely (distinct from marking a real part unavailable). Counted for evaluation."""
        c = next((x for x in self.components(pid) if x.id == cid), None)  # must belong to this project
        if not c:
            raise WorkflowError("component_not_found", f"Component {cid} not found")
        self.store.delete(cid)
        self.metrics.record("detection.false_positive", 1, "count", "live" if self.project(pid).mode == Mode.REAL else "simulated", type=c.type.value, was=c.status.value)
        self.log(pid, "decision", f"User discarded false detection '{c.name}'", component=cid)
        return self._sync(pid, "detection discarded")

    def put_components(self, pid: str, comps: list[ComponentInstance], reason: str = "manual inventory") -> dict:
        for c in comps:
            self.store.put(pid, "component", c.id, c)
        return self._sync(pid, reason)

    def update_component(self, pid: str, cid: str, patch: dict) -> ComponentInstance:
        c = self.store.get(cid, ComponentInstance)
        if not c:
            raise WorkflowError("component_not_found", f"Component {cid} not found")
        before_available = c.available
        if patch.get("type") and patch["type"] != c.type.value:
            self.metrics.record("classification.correction", 1, "count", "live" if self.project(pid).mode == Mode.REAL else "simulated", frm=c.type.value, to=patch["type"])
        for k in ("name", "type", "available", "orientation_deg"):
            if k in patch and patch[k] is not None:
                setattr(c, k, ComponentType(patch[k]) if k == "type" else patch[k])
        if "properties" in patch and patch["properties"] is not None:
            c.properties = {**c.properties, **{k: (float(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in patch["properties"].items()}}
        if patch.get("confirm"):
            c.status = Provenance.USER_CONFIRMED
        self.store.put(pid, "component", c.id, c)
        self.log(pid, "decision", f"User updated {c.name}", component=c.id, patch=patch)
        # A removal typed by the user is NOT a camera observation. If the part was seen in a frame, say that frame still shows it.
        manual = before_available and not c.available and c.bbox is not None
        if before_available == c.available:
            reason = "user correction"
        elif manual:
            reason = "manual override: marked unavailable by user (the last camera frame still shows it; not an observed removal)"
        else:
            reason = "component removed" if not c.available else "component restored"
        self._sync(pid, reason, extra={"manual_override": True, "last_frame_still_shows": True, "component": c.name} if manual else None)
        return c

    # ---- plan / validate ---------------------------------------------------------------
    async def plan(self, pid: str, goal: str | None = None) -> list[DesignCandidate]:
        p = self.project(pid)
        if goal:
            p.goal = goal
            self.store.put(pid, "project", pid, p)
        if p.stage in (Stage.OBSERVE, Stage.INTERPRET):
            raise WorkflowError("inventory_unconfirmed", "Scan and confirm components before planning.")
        if p.stage == Stage.CONFIRM:
            p = self.set_stage(pid, Stage.PLAN)  # returns the updated project; a stale p.stage would skip VALIDATE below
        inv = [c for c in self.components(pid) if c.available]
        st = self.state(pid)
        ctx = PlanContext(goal=p.goal, board_id=p.board, inventory=inv, prior_designs=self.designs(pid), failures=[r for rs in st.invalidated.values() for r in rs])
        _, reasoning = self._providers(p.mode)
        t_plan = time.monotonic()
        try:
            cands = await reasoning.propose_designs(ctx)
        except ProviderError as e:
            raise WorkflowError(e.code, e.message, e.hint) from e
        rprov = "live" if reasoning.live else "simulated"
        self.metrics.record("design.generation_ms", (time.monotonic() - t_plan) * 1000, "ms", rprov, source=getattr(reasoning, "name", "?"))
        out = []
        for d in cands:
            d.validation = validate_design(d, inv)  # deterministic post-validation; never trust provider
            for v in d.validation:
                self.metrics.record(f"validation.{v.status.value}", 1, "count", rprov, rule=v.rule)
            self.store.put(pid, "design", d.id, d)
            self.log(pid, "design_created", f"Design '{d.title}' proposed by {d.source}: {d.overall.value}", design_id=d.id)
            out.append(d)
        trace = getattr(reasoning, "last_trace", None)
        if trace:
            self.decision_traces[pid] = trace
        if p.stage in (Stage.PLAN, Stage.REDESIGN):
            self.set_stage(pid, Stage.VALIDATE)
        return out

    def approve_design(self, pid: str, did: str) -> list[AssemblyStep]:
        d = self.design(pid, did)
        d.validation = validate_design(d, [c for c in self.components(pid) if c.available])
        self.store.put(pid, "design", d.id, d)
        ok, why = execution_allowed(d)
        if not ok:
            raise WorkflowError("validation_blocked", f"Design cannot be approved: {why}", "Resolve BLOCKED/UNKNOWN validation results first.")
        st = self.state(pid)
        if not st.original_design_id:
            st.original_design_id = d.id
        st.active_design_id = d.id
        st.approved_for_execution = False
        steps = generate_assembly_steps(d)
        st.steps_by_design[d.id] = steps
        self._save_state(st)
        self.log(pid, "decision", f"User approved design '{d.title}'", design_id=d.id)
        t0 = self._redesign_started.pop(pid, None)
        if t0 is not None:
            sim = self.project(pid).mode == Mode.DEMO
            self.metrics.record("redesign.seconds", time.monotonic() - t0, "s", "simulated" if sim else "live", design_id=d.id)
        p = self.project(pid)
        if p.stage in (Stage.VALIDATE, Stage.REDESIGN):
            self.set_stage(pid, Stage.GUIDE)
        return steps

    def reject_design(self, pid: str, did: str, reason: str = "") -> None:
        self.log(pid, "decision", f"User rejected design {did}: {reason}", design_id=did)
        st = self.state(pid)
        if st.active_design_id == did:
            st.active_design_id, st.approved_for_execution = None, False
            self._save_state(st)

    def confirm_step(self, pid: str, did: str, index: int) -> list[AssemblyStep]:
        st = self.state(pid)
        steps = st.steps_by_design.get(did)
        if not steps:
            raise WorkflowError("no_steps", "Approve the design first.")
        for s in steps:
            if s.index == index:
                prior = [x for x in steps if x.index < index and not x.confirmed]
                if prior:
                    raise WorkflowError("steps_out_of_order", f"Confirm step {prior[0].index} first.")
                s.confirmed = True
        self._save_state(st)
        self.log(pid, "decision", f"Assembly step {index} confirmed by user", design_id=did)
        return steps

    # ---- execution gate ----------------------------------------------------------------
    def _assert_can_execute(self, pid: str) -> DesignCandidate:
        st = self.state(pid)
        if not st.active_design_id:
            raise WorkflowError("no_design", "No approved design.")
        d = self.design(pid, st.active_design_id)
        ok, why = execution_allowed(d)
        if not ok:
            raise WorkflowError("validation_blocked", f"Execution blocked: {why}")
        steps = st.steps_by_design.get(d.id, [])
        if not steps or not all(s.confirmed for s in steps):
            raise WorkflowError("assembly_unconfirmed", "Confirm every assembly step before testing.")
        return d

    async def attach_hardware(self, session: HardwareSession) -> dict:
        if self.hw:
            await self.hw.disconnect()
        session.on_latency = lambda ms: self.metrics.record("serial.latency_ms", ms, "ms", "simulated" if session.simulated else "live")
        info = await session.connect()
        self.hw = session
        return info

    def authorize_execution(self, pid: str) -> dict:
        """Explicit user approval of physical execution for the active design."""
        d = self._assert_can_execute(pid)
        if not self.hw or self.hw.status != "connected":
            raise WorkflowError("hardware_unavailable", "Connect a board first.")
        info = self.hw.info
        if info.get("led_pin") is not None and info["led_pin"] != d.behavior.led_pin:
            raise WorkflowError("firmware_pin_mismatch", f"Firmware LED pin {info['led_pin']} != design pin {d.behavior.led_pin}.", "Rebuild firmware with the design's pins.")
        st = self.state(pid)
        st.approved_for_execution = True
        self._save_state(st)
        self.hw.authorize(d.id, d.behavior.led_pin)
        self.log(pid, "decision", "User authorized physical execution", design_id=d.id, simulated=self.hw.simulated)
        if self.project(pid).stage == Stage.GUIDE:
            self.set_stage(pid, Stage.TEST)
        return {"authorized": d.id, "simulated": self.hw.simulated}

    # ---- tests -------------------------------------------------------------------------
    def _active(self, pid: str) -> DesignCandidate:
        st = self.state(pid)
        if not (st.active_design_id and st.approved_for_execution and self.hw and self.hw.authorized_design == st.active_design_id):
            raise WorkflowError("not_authorized", "Authorize execution for the active design first.")
        return self.design(pid, st.active_design_id)

    async def start_photoresistor_test(self, pid: str, duration_ms: int = 30000) -> dict:
        d = self._active(pid)
        if d.behavior.sensor_source != "photoresistor_adc":
            raise WorkflowError("wrong_design_type", "Active design is not a photoresistor design.")
        assert self.hw
        dm = "adc_low" if d.behavior.dark_means == "adc_low" else "adc_high"
        data = await self._hw(self.hw.run_test(int(d.behavior.threshold), dm, duration_ms))
        self._phase_buf[pid] = []
        self.log(pid, "hardware_test", "Autonomous on-device test started", design_id=d.id)
        return data

    async def capture_phase(self, pid: str, label: str, expected_led: bool, seconds: float = 2.0, optical: list[float] | None = None) -> dict:
        self._active(pid)
        assert self.hw
        t0 = len(self.hw.telemetry)
        await asyncio.sleep(seconds)
        samples = list(self.hw.telemetry)[t0:]
        self._phase_buf.setdefault(pid, []).append(Phase(label=label, samples=samples, expected_led=expected_led, optical_roi=optical or []))
        return {"label": label, "samples": len(samples)}

    async def finish_photoresistor_test(self, pid: str) -> VerificationResult:
        d = self._active(pid)
        assert self.hw
        acks = [c["cmd"] for c in self.hw.commands_sent if c["cmd"] == "run_test"]
        res = evaluate_photoresistor(d, self._phase_buf.get(pid, []), acks)
        return await self._record_verification(pid, d, res)

    async def phone_brightness(self, pid: str, value: float) -> dict:
        d = self._active(pid)
        assert self.hw
        if d.behavior.sensor_source != "phone_camera_brightness":
            raise WorkflowError("wrong_design_type", "Active design does not use the phone sensor.")
        want = value < d.behavior.threshold
        prev = self._phone_led.get(pid)
        # hysteresis: only act when crossing threshold by 10% of threshold
        margin = d.behavior.threshold * 0.1
        if prev is not None and abs(value - d.behavior.threshold) < margin:
            want = prev
        acked, reported, cmd = False, None, None
        if want != prev:
            data = await self._hw(self.hw.set_led(d.behavior.led_pin, want))
            acked, reported, cmd = True, data.get("led"), want
            self._phone_led[pid] = want
        self._phone_samples.setdefault(pid, []).append(PhoneSample(brightness=value, commanded_led=cmd if cmd is not None else prev, led_reported=reported, acked=acked))
        return {"brightness": value, "led": self._phone_led.get(pid), "commanded": cmd is not None, "simulated": self.hw.simulated}

    async def finish_phone_test(self, pid: str) -> VerificationResult:
        d = self._active(pid)
        assert self.hw
        res = evaluate_phone_light(d, self._phone_samples.get(pid, []), self.hw.simulated)
        return await self._record_verification(pid, d, res)

    async def _record_verification(self, pid: str, d: DesignCandidate, res: VerificationResult) -> VerificationResult:
        st = self.state(pid)
        st.verifications[d.id] = res
        self._save_state(st)
        self.log(pid, "hardware_test", res.summary, design_id=d.id, level=res.level.value, passed=res.passed)
        if self.project(pid).stage == Stage.TEST:
            self.set_stage(pid, Stage.COMPARE)
        return res

    async def _hw(self, coro: Any) -> dict:
        try:
            return await coro
        except HardwareError as e:
            raise WorkflowError(e.code, e.message, "Check cable, firmware and heartbeat status.") from e

    async def optical_command(self, pid: str, on: bool, phase: str) -> dict:
        """Drive the authorized LED for the optical test. The browser samples the camera around this call."""
        d = self._active(pid)
        assert self.hw
        if phase == "off_before":
            self._optical_t0[pid] = time.monotonic()
        data = await self._hw(self.hw.set_led(d.behavior.led_pin, on))
        return {"led_reported": data.get("led"), "t": time.monotonic() - self._optical_t0.get(pid, time.monotonic()), "simulated": self.hw.simulated}

    async def optical_evaluate(self, pid: str, samples: list[OpticalSample]) -> VerificationResult:
        d = self._active(pid)
        assert self.hw
        opt = evaluate_optical(samples)
        acked = any(c["cmd"] == "set_led" for c in self.hw.commands_sent)
        prior = self.state(pid).verifications.get(d.id)
        res = apply_optical(prior, d, opt, self.hw.simulated, acked)
        self.metrics.record("optical.delta", opt.delta or 0.0, "luma", "simulated" if self.hw.simulated else "live", outcome=opt.outcome)
        return await self._record_verification(pid, d, res)

    async def emergency_stop(self, pid: str | None = None) -> None:
        if self.hw and self.hw.status in ("connected", "lost"):
            try:
                await self.hw.stop()
            except HardwareError:
                self.hw.revoke()
        if pid:
            st = self.state(pid)
            st.approved_for_execution = False
            self._save_state(st)
            self.log(pid, "decision", "Emergency stop")

    # ---- compare -----------------------------------------------------------------------
    def comparison(self, pid: str) -> dict:
        st = self.state(pid)
        designs = {d.id: d for d in self.designs(pid)}
        orig = designs.get(st.original_design_id or "")
        cur = designs.get(st.active_design_id or "")

        def view(d: DesignCandidate | None) -> dict | None:
            if not d:
                return None
            v = st.verifications.get(d.id)
            return {
                "id": d.id, "title": d.title, "sensor": d.behavior.sensor_source, "overall": d.overall.value,
                "validation": [r.model_dump(mode="json") for r in d.validation],
                "invalidated_reasons": st.invalidated.get(d.id, []),
                "substitute_notes": d.substitute_notes, "tradeoffs": d.tradeoffs,
                "verification": v.model_dump(mode="json") if v else None,
            }

        removed = [e.data.get("removed") for e in self.revisions(pid) if e.kind == "inventory_change" and e.data.get("removed")]
        return {"original": view(orig), "revised": view(cur if cur and cur.id != (orig.id if orig else None) else None), "removed_components": [n for r in removed for n in r], "limitations": [
            "Estimated currents are calculations, not measurements.",
            "A device-reported LED state is not proof of illumination without independent optical evidence.",
            "A phone-camera brightness sensor measures the phone's view, not the light at the LED.",
        ]}

    def telemetry_summary(self) -> str:
        if not self.hw or not self.hw.telemetry:
            return ""
        last = self.hw.telemetry[-1]
        return f"source={last.source} adc={last.adc} led={last.led_state}"
