"""Muse Spark OPEN (open-weight) reasoning via an inference partner, OpenAI-compatible Chat Completions.

This is NOT the Meta Model API and NOT necessarily Muse Spark 1.3. Every proposal it produces is tagged
`source="open-weight:<model>@<host>"` and the UI states that. It reuses the same closed tool set and the same
deterministic validation, so the application architecture is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlparse

import cv2
import numpy as np

from ..config import Settings
from ..designs import default_pins
from ..models import BBox, ComponentInstance, ComponentType, DesignCandidate, Provenance
from ..tools import ToolContext, call_tool, tool_specs
from .base import Detection, PlanContext, ProviderError, Usage
from .meta import SYSTEM_PROMPT, MetaClient, scene_to_components


def chat_tools() -> list[dict]:
    """Chat Completions nests the function definition (unlike the Responses API's flat form)."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tool_specs()]


class OpenWeightReasoning:
    live = True
    family = "meta-open-weight (partner-hosted, not Meta Model API)"
    MAX_ROUNDS = 5

    def __init__(self, settings: Settings, client: MetaClient | None = None, metrics: Any = None):
        self.s = settings
        self.metrics = metrics
        self.model = settings.ow_model
        self.host = urlparse(settings.ow_base_url).netloc or "unconfigured"
        self.name = f"open-weight:{self.model or 'unset'}@{self.host}"
        self.usage_meter = Usage(budget=settings.META_MAX_REQUESTS)
        self.client = client or MetaClient(
            settings, self.usage_meter, metrics, "openweight",
            base_url=settings.ow_base_url or "https://invalid.local", api_key=settings.ow_key,
            path="/chat/completions", label="Open-weight inference partner",
        )
        self.last_trace: list[dict] = []
        self.tools_supported = True  # flips to False if the provider rejects `tools` (recorded in the trace)

    async def propose_designs(self, ctx: PlanContext) -> list[DesignCandidate]:
        if not self.s.open_weight_configured:
            raise ProviderError("no_api_key", "Open-weight backend is not configured.", "Set OPEN_WEIGHT_BASE_URL, OPEN_WEIGHT_API_KEY and OPEN_WEIGHT_MODEL in .env.")
        tctx = ToolContext(ctx.inventory, ctx.board_id, ctx.goal, ctx.telemetry_summary)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Goal: {ctx.goal}\nBoard: {ctx.board_id}\nKnown failures: {ctx.failures or 'none'}\n"
                "Propose every feasible design using only available components. If a required component is unavailable, "
                "consider substitute sensing such as phone_camera_brightness and say it is a substitute."
            )},
        ]
        trace: list[dict] = []
        t0 = time.monotonic()
        for _ in range(self.MAX_ROUNDS):
            body = {"model": self.model, "messages": messages}
            if self.tools_supported:
                body["tools"] = chat_tools()
            try:
                resp = await self.client.json(body)
            except ProviderError as e:
                # Some providers/models reject the `tools` field. Detect that explicitly, remember it, use the constrained-JSON path.
                if e.code == "bad_request" and self.tools_supported and "tool" in e.message.lower():
                    self.tools_supported = False
                    trace.append({"round": len(trace) + 1, "mode": "tools_rejected_by_provider", "text": e.message[:300]})
                    break
                raise
            msg = (resp.get("choices") or [{}])[0].get("message", {})
            calls = msg.get("tool_calls") or []
            trace.append({"round": len(trace) + 1, "tool_calls": [c.get("function", {}).get("name") for c in calls], "text": (msg.get("content") or "")[:1000], "usage": resp.get("usage")})
            if not calls:
                break
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for c in calls:
                fn = c.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = call_tool(tctx, fn.get("name", ""), args)  # same closed tool set; unknown tools rejected
                messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": json.dumps(result)})
        if not tctx.candidates:
            await self._json_mode(tctx, messages, trace)
        self._record_usage(trace)
        if self.metrics:
            self.metrics.record("openweight.design_total_ms", (time.monotonic() - t0) * 1000, "ms", "live", rounds=len(trace), model=self.model)
        self.last_trace = trace + [{"tool_trace": tctx.trace}]
        if not tctx.candidates:
            raise ProviderError("no_valid_proposal", "The open-weight model produced no circuit proposal.", "Retry, or use the deterministic planner explicitly.")
        for d in tctx.candidates.values():
            d.source = f"open-weight:{self.model}@{self.host}"
        return list(tctx.candidates.values())

    def _record_usage(self, trace: list[dict]) -> None:
        """Per-call tokens are recorded by MetaClient (provider-reported). Cost is an estimate and only exists if prices are configured."""
        if not self.metrics:
            return
        tin = sum((t.get("usage") or {}).get("prompt_tokens", 0) for t in trace)
        tout = sum((t.get("usage") or {}).get("completion_tokens", 0) for t in trace)
        pin, pout = self.s.OPEN_WEIGHT_PRICE_IN_PER_M, self.s.OPEN_WEIGHT_PRICE_OUT_PER_M
        if pin is not None and pout is not None:
            self.metrics.record("openweight.cost_usd_estimate", (tin * pin + tout * pout) / 1e6, "usd", "live", model=self.model, basis="owner-configured prices")

    async def _json_mode(self, tctx: ToolContext, messages: list[dict], trace: list[dict]) -> None:
        """Fallback for models/providers without tool calling: ask for a tiny JSON choice, then let trusted code build it.

        The model may only pick from an enumerated list of sensor sources; connections, pins and validation are still
        produced by the same trusted generators and checked afterwards. Anything else in the reply is ignored.
        """
        allowed = ["photoresistor_adc", "phone_camera_brightness"]
        messages = [m for m in messages if m.get("role") in ("system", "user")][:2]
        messages.append({"role": "user", "content": f'Reply with JSON only, no prose: {{"sensor_sources": [...]}} where each item is one of {allowed}, listing every feasible design for the inventory.'})
        resp = await self.client.json({"model": self.model, "messages": messages})
        text = ((resp.get("choices") or [{}])[0].get("message", {}).get("content")) or ""
        trace.append({"round": len(trace) + 1, "mode": "json_fallback", "text": text[:600], "usage": resp.get("usage")})
        start, end = text.find("{"), text.rfind("}")
        try:
            chosen = json.loads(text[start : end + 1]).get("sensor_sources", []) if start >= 0 else []
        except (json.JSONDecodeError, AttributeError):
            chosen = []
        for src in dict.fromkeys(x for x in chosen if isinstance(x, str) and x in allowed):
            call_tool(tctx, "propose_circuit", {"sensor_source": src})

    async def interpret_scene(self, image: bytes, detections: list[Detection]) -> list[ComponentInstance]:
        return scene_to_components(detections)

    def usage(self) -> dict:
        return self.usage_meter.as_dict()


VLM_TYPES = [t.value for t in ComponentType if t not in (ComponentType.UNKNOWN,)]
VLM_PROMPT = (
    "You are the perception step of an electronics-bench assistant. List the electronic parts visible in the photo. "
    f"Reply with JSON only: {{\"components\": [{{\"type\": one of {VLM_TYPES}, \"label\": short text, "
    "\"box\": [x1, y1, x2, y2] as fractions 0-1 of image width/height or null, \"confidence\": 0-1}}]}. "
    "Do NOT guess resistor values, LED colours or pin functions. Only list parts you can actually see."
)


def parse_vlm_inventory(text: str, width: int, height: int, limit: int = 20) -> list[ComponentInstance]:
    """Strictly turn a model's JSON into inventory. Unknown types become `unknown`; boxes outside 0-1 are dropped; values stay unknown."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(text[start : end + 1]).get("components", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out: list[ComponentInstance] = []
    for i, it in enumerate(items[:limit] if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        t = ComponentType(it["type"]) if it.get("type") in VLM_TYPES else ComponentType.UNKNOWN
        box = it.get("box")
        bbox = None
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box) and box[2] > box[0] and box[3] > box[1]:
            bbox = BBox(x1=box[0] * width, y1=box[1] * height, x2=box[2] * width, y2=box[3] * height, img_w=width, img_h=height)
        conf = it.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) and 0 <= conf <= 1 else None
        props: dict = {"resistance_ohm": None} if t == ComponentType.RESISTOR else ({"forward_v": None} if t == ComponentType.LED else {})
        label = str(it.get("label") or t.value)[:40]
        out.append(
            ComponentInstance(
                id=f"vlm_{hashlib.sha1(f'{i}{t.value}{box}'.encode()).hexdigest()[:8]}",
                type=t, name=f"{label} {i + 1}", bbox=bbox, pins=default_pins(t), properties=props, confidence=conf,
                status=Provenance.UNCERTAIN if (conf is None or conf < 0.6 or t == ComponentType.UNKNOWN) else Provenance.DETECTED,
            )
        )
    return out


class VlmPerception:
    """Whole-frame inventory from the open-weight multimodal model. Not SAM: no masks, boxes are the model's own estimates."""

    live = True

    def __init__(self, reasoning: OpenWeightReasoning):
        self.r = reasoning
        self.name = f"vlm:{reasoning.model}@{reasoning.host}"
        self.last_raw: str = ""

    def ensure_ready(self) -> None:
        if not self.r.s.open_weight_configured:
            raise ProviderError("no_api_key", "Open-weight backend is not configured.", "Set HF_TOKEN, BASETEN_API_KEY or OPEN_WEIGHT_* in .env.")

    async def scan_scene(self, image: bytes) -> list[ComponentInstance]:
        if not self.r.s.open_weight_configured:
            raise ProviderError("no_api_key", "Open-weight backend is not configured.", "Set HF_TOKEN (or OPEN_WEIGHT_*) in .env.")
        arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ProviderError("bad_image", "Image could not be decoded.")
        h, w = arr.shape[:2]
        mime = "image/png" if image[1:4] == b"PNG" else "image/webp" if image[8:12] == b"WEBP" else "image/jpeg"
        url = f"data:{mime};base64," + base64.b64encode(image).decode()
        body = {
            "model": self.r.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": VLM_PROMPT}, {"type": "image_url", "image_url": {"url": url}}]}],
        }
        resp = await self.r.client.json(body)
        text = ((resp.get("choices") or [{}])[0].get("message", {}).get("content")) or ""
        self.last_raw = text
        return parse_vlm_inventory(text, w, h)

    def usage(self) -> dict:
        return self.r.usage()
