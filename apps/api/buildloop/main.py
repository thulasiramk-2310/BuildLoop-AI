"""FastAPI application: REST + WebSocket surface over the Engine."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .agent import agent_view
from .boards import BOARDS
from .config import ROOT, Settings, get_settings
from .db import Store
from .diagnostics import diagnostics as hw_diagnostics
from .hardware.session import HardwareError, HardwareSession
from .hardware.transport import FakeDeviceTransport, SerialTransport, list_ports
from .metrics import Metrics
from .models import ApiError, ComponentInstance, ComponentType, Mode, Provenance, Stage
from .optical import OpticalSample
from .providers import make_provider_factory
from .providers.base import ProviderError
from .providers.demo import H, W, demo_components
from .validation import validate_design
from .workflow import Engine, WorkflowError

logging.basicConfig(level=logging.INFO, format='{"t":"%(asctime)s","lvl":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("buildloop")


class NewProject(BaseModel):
    name: str
    goal: str = "Build a light that turns on when the environment gets dark."
    mode: Mode = Mode.DEMO
    board: str = "esp32-devkitc-v4"


class ComponentPatch(BaseModel):
    name: str | None = None
    type: ComponentType | None = None
    available: bool | None = None
    properties: dict | None = None
    confirm: bool = False


class NewComponent(BaseModel):
    type: ComponentType
    name: str
    properties: dict = {}


class PlanReq(BaseModel):
    goal: str | None = None


class ConnectReq(BaseModel):
    port: str | None = None
    simulated: bool = False
    baud: int = 115200
    project_id: str | None = None
    # Opening a real serial port can reset the board. The caller must restate the port and approve explicitly.
    approve_open_port: bool = False
    confirm_port: str | None = None


class OpticalCmd(BaseModel):
    on: bool
    phase: str


class OpticalEval(BaseModel):
    samples: list[OpticalSample]


class PhaseReq(BaseModel):
    label: str
    expected_led: bool
    seconds: float = 2.0
    optical: list[float] = []


class BrightnessReq(BaseModel):
    value: float


class AmbientReq(BaseModel):
    value: float


class StageReq(BaseModel):
    stage: Stage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.DATABASE_URL)
    metrics = Metrics(store)
    engine = Engine(store, make_provider_factory(settings, metrics), metrics)
    phone_seen: dict[str, float] = {}
    phone_frames: dict[str, bytes] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.emergency_stop()
        if engine.hw:
            await engine.hw.disconnect()
        store.close()

    app = FastAPI(title="BuildLoop AI", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS.split(","), allow_methods=["*"], allow_headers=["*"], allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?")

    def err(status: int, code: str, message: str, hint: str = "") -> JSONResponse:
        return JSONResponse(ApiError(code=code, message=message, hint=hint).model_dump(), status_code=status)

    @app.exception_handler(WorkflowError)
    async def _wf(_: Request, e: WorkflowError):
        status = 404 if e.code.endswith("not_found") else 409
        return err(status, e.code, e.message, e.hint)

    @app.exception_handler(ProviderError)
    async def _pe(_: Request, e: ProviderError):
        status = {"no_api_key": 503, "auth_invalid": 502, "rate_limited": 429, "budget_exhausted": 429, "payment_required": 402}.get(e.code, 502)
        return err(status, e.code, e.message, e.hint)

    @app.exception_handler(HardwareError)
    async def _he(_: Request, e: HardwareError):
        return err(409, e.code, e.message)

    def proj(pid: str):
        return engine.project(pid)

    # ---- health / meta --------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "meta_configured": settings.meta_configured, "open_weight_configured": settings.open_weight_configured, "reasoning_backend": settings.REASONING_BACKEND, "perception_backend": settings.PERCEPTION_BACKEND, "open_weight_model": settings.ow_model or None, "boards": sorted(BOARDS), "hardware": engine.hw.snapshot() if engine.hw else None}

    @app.get("/api/network")
    def network():
        """LAN addresses for phone pairing (phone and laptop must share Wi-Fi)."""
        import socket

        ips: list[str] = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))  # no packet is sent; selects the outbound interface
                ips.append(s.getsockname()[0])
        except OSError:
            pass
        return {"lan_ips": ips}

    @app.get("/api/meta/usage")
    def usage():
        seg_d, rea_d = engine._providers(Mode.DEMO)
        seg_l, rea_l = engine._providers(Mode.REAL)
        return {"live": {"segmentation": seg_l.usage(), "reasoning": rea_l.usage(), "configured": settings.meta_configured}, "demo": {"segmentation": seg_d.usage(), "reasoning": rea_d.usage()}}

    # ---- projects -----------------------------------------------------------------------
    @app.post("/api/projects")
    def create_project(b: NewProject):
        if b.board not in BOARDS:
            raise HTTPException(400, f"unsupported board {b.board}")
        return engine.create_project(b.name, b.goal, b.mode, b.board)

    @app.get("/api/projects")
    def list_projects():
        return engine.list_projects()

    @app.get("/api/projects/{pid}")
    def project_state(pid: str):
        p = proj(pid)
        st = engine.state(pid)
        return {
            "project": p,
            "components": engine.components(pid),
            "designs": engine.designs(pid),
            "state": st,
            "revisions": engine.revisions(pid),
            "comparison": engine.comparison(pid),
            "decision_trace": engine.decision_traces.get(pid, []),
            "hardware": engine.hw.snapshot() if engine.hw else None,
        }

    @app.post("/api/projects/{pid}/stage")
    def set_stage(pid: str, b: StageReq):
        return engine.set_stage(pid, b.stage)

    # ---- perception ----------------------------------------------------------------------
    @app.get("/api/perception/demo-frame.jpg")
    def demo_frame():
        """Synthetic labelled image used by Demo Mode. Not a real photograph."""
        img = np.full((H, W, 3), 38, np.uint8)
        for c in demo_components():
            b = c.bbox
            assert b
            col = {"esp32": (160, 120, 40), "breadboard": (200, 200, 190), "led": (60, 60, 220), "resistor": (90, 160, 200), "photoresistor": (60, 160, 90)}.get(c.type.value, (120, 120, 120))
            cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), col, -1 if c.type.value != "breadboard" else 2)
            cv2.putText(img, c.name, (int(b.x1), int(b.y1) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
        cv2.putText(img, "SYNTHETIC DEMO FRAME", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 200, 255), 2)
        _ok, buf = cv2.imencode(".jpg", img)
        return Response(buf.tobytes(), media_type="image/jpeg")

    @app.post("/api/perception/{pid}/scan")
    async def scan(pid: str, image: UploadFile = File(...)):
        data = await image.read()
        if len(data) > 8_000_000:
            raise HTTPException(413, "image too large")
        return await engine.scan(pid, data)

    # ---- inventory -------------------------------------------------------------------------
    @app.get("/api/inventory/{pid}")
    def inventory(pid: str):
        return engine.components(pid)

    @app.post("/api/inventory/{pid}")
    def add_component(pid: str, b: NewComponent):
        from .designs import default_pins

        c = ComponentInstance(type=b.type, name=b.name, pins=default_pins(b.type), properties=b.properties, status=Provenance.USER_CONFIRMED)
        engine.put_components(pid, [c], "user added component")
        return c

    @app.delete("/api/inventory/{pid}/{cid}")
    def discard_component(pid: str, cid: str):
        return engine.discard_component(pid, cid)

    @app.patch("/api/inventory/{pid}/{cid}")
    def patch_component(pid: str, cid: str, b: ComponentPatch):
        return engine.update_component(pid, cid, b.model_dump(exclude_unset=True))

    # ---- designs / validation / assembly -------------------------------------------------
    @app.post("/api/designs/{pid}/plan")
    async def plan(pid: str, b: PlanReq):
        return await engine.plan(pid, b.goal)

    @app.get("/api/designs/{pid}")
    def designs(pid: str):
        return engine.designs(pid)

    @app.post("/api/designs/{pid}/{did}/approve")
    def approve(pid: str, did: str):
        return engine.approve_design(pid, did)

    @app.post("/api/designs/{pid}/{did}/reject")
    def reject(pid: str, did: str):
        engine.reject_design(pid, did)
        return {"ok": True}

    @app.post("/api/validation/{pid}/{did}")
    def revalidate(pid: str, did: str):
        d = engine.design(pid, did)
        d.validation = validate_design(d, [c for c in engine.components(pid) if c.available])
        store.put(pid, "design", d.id, d)
        return d.validation

    @app.get("/api/assembly/{pid}/{did}")
    def steps(pid: str, did: str):
        return engine.state(pid).steps_by_design.get(did, [])

    @app.post("/api/assembly/{pid}/{did}/{index}/confirm")
    def confirm_step(pid: str, did: str, index: int):
        return engine.confirm_step(pid, did, index)

    # ---- hardware -------------------------------------------------------------------------
    @app.post("/api/hardware/validate")
    async def hardware_validate():
        """First-physical-session checks. Needs an already approved, connected session. Never actuates."""
        if not engine.hw:
            return err(409, "hardware_unavailable", "Connect a board first.", "Select the port and approve opening it.")
        return await engine.hw.first_validation()

    @app.get("/api/agent/{pid}")
    def agent(pid: str):
        """What the engineering agent will do next, why, what needs a human, and when it replans."""
        return agent_view(engine, pid)

    @app.get("/api/hardware/diagnostics")
    def hardware_diagnostics():
        """Read-only: enumerates ports without opening any."""
        return hw_diagnostics(engine.hw.snapshot() if engine.hw else None)

    @app.get("/api/metrics")
    def get_metrics():
        return {"summary": metrics.summary(), "count": len(metrics.all())}

    @app.get("/api/metrics/raw")
    def get_metrics_raw():
        return metrics.all()

    @app.post("/api/verification/{pid}/optical/command")
    async def optical_command(pid: str, b: OpticalCmd):
        return await engine.optical_command(pid, b.on, b.phase)

    @app.post("/api/verification/{pid}/optical/evaluate")
    async def optical_evaluate(pid: str, b: OpticalEval):
        return await engine.optical_evaluate(pid, b.samples)

    @app.post("/api/phone/{pid}/snapshot")
    async def phone_snapshot(pid: str, image: UploadFile = File(...)):
        import time

        data = await image.read()
        if len(data) > 8_000_000:
            raise HTTPException(413, "image too large")
        phone_frames[pid] = data
        phone_seen[pid] = time.monotonic()
        return {"bytes": len(data)}

    @app.get("/api/phone/{pid}/snapshot.jpg")
    def phone_snapshot_get(pid: str):
        if pid not in phone_frames:
            raise HTTPException(404, "no phone snapshot yet")
        return Response(phone_frames[pid], media_type="image/jpeg")

    @app.get("/api/phone/{pid}/status")
    def phone_status(pid: str):
        import time

        seen = phone_seen.get(pid)
        age = None if seen is None else time.monotonic() - seen
        return {"connected": age is not None and age < 3.0, "last_seen_s_ago": age, "has_snapshot": pid in phone_frames}

    @app.get("/api/hardware/ports")
    def ports():
        return list_ports()

    @app.get("/api/hardware/status")
    def hw_status():
        return engine.hw.snapshot() if engine.hw else {"status": "disconnected"}

    @app.post("/api/hardware/connect")
    async def hw_connect(b: ConnectReq):
        if b.simulated:
            if not b.project_id or proj(b.project_id).mode != Mode.DEMO:
                raise HTTPException(400, "Simulated hardware is allowed only in Demo Mode projects.")
            transport = FakeDeviceTransport()
        elif b.port:
            if b.port not in [p.device for p in list_ports()]:
                raise HTTPException(400, f"unknown serial port {b.port}")
            if not b.approve_open_port or b.confirm_port != b.port:
                return err(400, "approval_required", f"Opening {b.port} needs explicit approval.", "Tick the approval box and confirm the port name. Opening a port may reset the board.")
            transport = SerialTransport(b.port, b.baud)
        else:
            raise HTTPException(400, "port or simulated required")
        sess = HardwareSession(transport)
        try:
            info = await engine.attach_hardware(sess)
        except HardwareError as e:
            return err(409, e.code, e.message, "Flash BuildLoop firmware (docs/hardware/SETUP.md) and select the correct port.")
        return {"info": info, "simulated": sess.simulated}

    @app.post("/api/hardware/disconnect")
    async def hw_disconnect():
        if engine.hw:
            await engine.hw.disconnect()
            engine.hw = None
        return {"ok": True}

    @app.post("/api/hardware/{pid}/authorize")
    def authorize(pid: str):
        return engine.authorize_execution(pid)

    @app.post("/api/hardware/{pid}/stop")
    async def stop(pid: str):
        await engine.emergency_stop(pid)
        return {"ok": True}

    @app.post("/api/hardware/sim/ambient")
    def sim_ambient(b: AmbientReq):
        if not engine.hw or not isinstance(engine.hw.transport, FakeDeviceTransport):
            raise HTTPException(400, "No simulated device connected.")
        engine.hw.transport.ambient = max(0.0, min(1.0, b.value))
        return {"ambient": engine.hw.transport.ambient, "simulated": True}

    @app.post("/api/verification/{pid}/photoresistor/start")
    async def vstart(pid: str):
        return await engine.start_photoresistor_test(pid)

    @app.post("/api/verification/{pid}/phase")
    async def vphase(pid: str, b: PhaseReq):
        return await engine.capture_phase(pid, b.label, b.expected_led, b.seconds, b.optical)

    @app.post("/api/verification/{pid}/photoresistor/finish")
    async def vfinish(pid: str):
        return await engine.finish_photoresistor_test(pid)

    @app.post("/api/verification/{pid}/phone/brightness")
    async def vphone(pid: str, b: BrightnessReq):
        return await engine.phone_brightness(pid, b.value)

    @app.post("/api/verification/{pid}/phone/finish")
    async def vphone_finish(pid: str):
        return await engine.finish_phone_test(pid)

    @app.get("/api/verification/{pid}")
    def verifications(pid: str):
        return engine.state(pid).verifications

    @app.get("/api/revisions/{pid}")
    def revisions(pid: str):
        return engine.revisions(pid)

    @app.get("/api/compare/{pid}")
    def compare(pid: str):
        return engine.comparison(pid)

    # ---- websockets ----------------------------------------------------------------------
    @app.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket):
        await ws.accept()
        if not engine.hw:
            await ws.send_json({"type": "hardware_status", "status": "disconnected"})
            await ws.close()
            return
        q = engine.hw.subscribe()
        try:
            while True:
                await ws.send_json(await q.get())
        except WebSocketDisconnect:
            pass
        finally:
            engine.hw.unsubscribe(q) if engine.hw else None

    @app.websocket("/ws/phone/{pid}")
    async def ws_phone(ws: WebSocket, pid: str):
        """Phone camera sends {"brightness": 0..1}; backend converts to whitelisted LED commands."""
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_json()
                import time

                phone_seen[pid] = time.monotonic()
                if msg.get("ping"):
                    await ws.send_json({"type": "pong"})
                    continue
                try:
                    out = await engine.phone_brightness(pid, float(msg["brightness"]))
                    await ws.send_json({"type": "state", **out})
                except (WorkflowError, KeyError, ValueError, TypeError) as e:
                    await ws.send_json({"type": "error", "message": getattr(e, "message", str(e))})
        except WebSocketDisconnect:
            pass

    dist = ROOT / "apps" / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
