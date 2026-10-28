"""Meta Model API providers: Muse Spark (reasoning) and SAM 3.1 (segmentation).

Only this process holds MODEL_API_KEY. Request shapes follow https://dev.meta.ai/docs/sam/segmenting
(Responses API, SSE). Muse Spark uses the same /responses endpoint; its structured-output and tool-call
shapes follow the OpenAI-compatible Responses format and are UNVERIFIED against the live service
until a key is available (see docs/demo/KNOWN_LIMITATIONS.md).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import cv2
import httpx
import numpy as np
from meta_sam_parser import (
    InvalidSegmentationMaskError,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_svg_path,
    image_segmentation_format,
    parse_responses_stream,
)

from ..config import Settings
from ..designs import default_pins
from ..models import BBox, ComponentInstance, ComponentType, DesignCandidate, Provenance
from ..tools import ToolContext, call_tool, tool_specs
from .base import Detection, PlanContext, ProviderError, Usage

log = logging.getLogger("buildloop.meta")

CONCEPT_TO_TYPE = {
    "esp32 development board": ComponentType.ESP32,
    "breadboard": ComponentType.BREADBOARD,
    "led": ComponentType.LED,
    "resistor": ComponentType.RESISTOR,
    "photoresistor": ComponentType.PHOTORESISTOR,
    "pushbutton": ComponentType.PUSHBUTTON,
    "jumper wire": ComponentType.JUMPER_WIRE,
}


class MetaClient:
    def __init__(self, settings: Settings, usage: Usage, metrics: Any = None, kind: str = "meta", *, base_url: str | None = None, api_key: str | None = None, path: str = "/responses", label: str = "Meta API"):
        self.s, self.usage, self.metrics, self.kind = settings, usage, metrics, kind
        self.base_url = base_url or settings.META_BASE_URL
        self.key = (api_key if api_key is not None else settings.MODEL_API_KEY).strip()
        self.path, self.label = path, label
        self.last_usage: dict = {}
        self._last = 0.0
        self._lock = asyncio.Lock()

    def _headers(self, stream: bool) -> dict:
        if not self.key:
            raise ProviderError("no_api_key", f"{self.label} credentials are not set.", "Set them in .env (never in chat) or use Demo Mode.")
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", **({"Accept": "text/event-stream"} if stream else {})}

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self.s.META_MIN_INTERVAL_S - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    @staticmethod
    def _envelope(r: httpx.Response) -> str:
        """OpenAI-style error envelope: {"error": {"message","type","param","code"}}."""
        try:
            e = r.json().get("error", {})
            return f"{e.get('type', '')}: {e.get('message', '')}".strip(": ") or r.text[:300]
        except (ValueError, AttributeError):
            return r.text[:300]

    def _raise_for(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise ProviderError("auth_invalid", f"{self.label} rejected the credentials.", "Check MODEL_API_KEY.")
        if r.status_code == 404:
            raise ProviderError("model_not_found", f"{self.label}: model or endpoint not found ({self._envelope(r)})", "Check OPEN_WEIGHT_MODEL against the provider catalog (scripts/baseten_audit.py). Nothing was substituted.")
        if r.status_code == 402:
            raise ProviderError("payment_required", f"{self.label}: no remaining credits ({self._envelope(r)})", "Add pre-paid credits (or a plan) with the provider, then retry. Nothing was simulated.")
        if r.status_code == 429:
            raise ProviderError("rate_limited", f"{self.label} rate limit hit.", f"Retry-After: {r.headers.get('retry-after', 'n/a')}")
        if r.status_code >= 500:
            raise ProviderError("upstream_error", f"{self.label} error {r.status_code}: {self._envelope(r)}")
        if r.status_code >= 400:
            raise ProviderError("bad_request", f"{self.label} rejected the request ({r.status_code}): {self._envelope(r)}")

    async def _with_retry(self, fn):
        self.usage.check()
        last: ProviderError | None = None
        for attempt in range(self.s.META_RETRIES):
            await self._throttle()
            t0 = time.monotonic()
            try:
                out = await fn()
                self.usage.record(t0)
                if self.metrics:
                    self.metrics.record(f"{self.kind}.latency_ms", (time.monotonic() - t0) * 1000, "ms", "live", attempt=attempt)
                return out
            except ProviderError as e:
                last = e
                self.usage.errors += 1
                if e.code not in ("rate_limited", "upstream_error", "timeout", "network"):
                    raise
            except httpx.TimeoutException:
                last = ProviderError("timeout", f"{self.label} timed out.")
                self.usage.errors += 1
            except httpx.TransportError as e:
                last = ProviderError("network", f"Network error: {e}")
                self.usage.errors += 1
            await asyncio.sleep(min(30.0, 2.0**attempt))
        assert last
        raise last

    async def sse(self, body: dict) -> list[dict]:
        """POST streaming; return decoded SSE events. Cancellation propagates via asyncio."""
        headers = self._headers(True)

        async def go() -> list[dict]:
            events: list[dict] = []
            async with (
                httpx.AsyncClient(base_url=self.base_url, timeout=self.s.META_TIMEOUT_S) as c,
                c.stream("POST", self.path, json=body, headers=headers) as r,
            ):
                if r.status_code >= 400:
                    await r.aread()
                    self._raise_for(r)
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload and payload != "[DONE]":
                            try:
                                events.append(json.loads(payload))
                            except json.JSONDecodeError:
                                continue
            return events

        return await self._with_retry(go)

    async def json(self, body: dict) -> dict:
        headers = self._headers(False)

        async def go() -> dict:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.s.META_TIMEOUT_S) as c:
                r = await c.post(self.path, json=body, headers=headers)
                self._raise_for(r)
                data = r.json()
                u = data.get("usage") or {}
                self.last_usage = u
                if self.metrics and u:
                    # Responses API names: input_tokens/output_tokens. Chat Completions names: prompt_tokens/completion_tokens.
                    self.metrics.record(f"{self.kind}.input_tokens", u.get("input_tokens", u.get("prompt_tokens", 0)), "tokens", "live")
                    self.metrics.record(f"{self.kind}.output_tokens", u.get("output_tokens", u.get("completion_tokens", 0)), "tokens", "live")
                return data

        return await self._with_retry(go)


async def _aiter(events: list[dict]) -> AsyncIterator[dict]:
    for e in events:
        yield e


async def parse_sam_events(events: list[dict], img_w: int, img_h: int, concept: str) -> list[Detection]:
    """Decode SAM SSE events into Detections via the official parser. Empty output is a normal result."""
    parsed = parse_responses_stream(_aiter(events), image_segmentation_format())
    async for _ in parsed:
        pass
    result = await parsed.final_result()
    boxes: dict[str, SegmentationBoxRecord] = {}
    masks: dict[str, SegmentationMaskRecord] = {}
    for rec in result.records:
        if isinstance(rec, SegmentationBoxRecord):
            boxes[rec.object_id] = rec
        elif isinstance(rec, SegmentationMaskRecord):
            masks[rec.object_id] = rec
    dets: list[Detection] = []
    for oid, b in boxes.items():
        d = Detection(concept=concept, box=(b.left, b.top, b.right, b.bottom), img_w=img_w, img_h=img_h, confidence=b.confidence)
        m = masks.get(oid)
        if m is not None:
            try:
                d.mask_path = decode_mask_to_svg_path(m.mask)
                d.mask_bounds = (m.bounds.left, m.bounds.top, m.bounds.right, m.bounds.bottom)
                if d.confidence is None:
                    d.confidence = m.confidence
            except (InvalidSegmentationMaskError, ValueError) as e:  # invalid mask: keep the box, drop the mask
                log.warning("dropping invalid SAM mask for %s: %s", concept, e)
        dets.append(d)
    return dets


class MetaSegmentation:
    name = "meta-sam-3.1"
    live = True

    def __init__(self, settings: Settings, client: MetaClient | None = None, metrics: Any = None):
        self.s = settings
        self.metrics = metrics
        self.usage_meter = Usage(budget=settings.META_MAX_REQUESTS)
        self.client = client or MetaClient(settings, self.usage_meter, metrics, "sam")
        self._cache: dict[str, list[Detection]] = {}

    def ensure_ready(self) -> None:
        self.client._headers(True)  # raises no_api_key

    async def segment(self, image: bytes, concept: str) -> list[Detection]:
        self.client._headers(True)  # fail fast with no_api_key before any image work
        key = hashlib.sha256(image).hexdigest() + "|" + concept
        if key in self._cache:
            self.usage_meter.cache_hits += 1
            return self._cache[key]
        arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ProviderError("bad_image", "Image could not be decoded.")
        h, w = arr.shape[:2]
        mime = "image/png" if image[1:4] == b"PNG" else "image/webp" if image[8:12] == b"WEBP" else "image/jpeg"
        data_url = f"data:{mime};base64," + base64.b64encode(image).decode()
        body = {
            "model": self.s.META_SEGMENTATION_MODEL,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": concept}, {"type": "input_image", "image_url": data_url}]}],
            "stream": True,
            "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
        }
        events = await self.client.sse(body)
        dets = await parse_sam_events(events, w, h, concept)
        self._cache[key] = dets
        return dets

    def usage(self) -> dict:
        return self.usage_meter.as_dict()


SYSTEM_PROMPT = (
    "You are the engineering planner inside BuildLoop AI. Design low-voltage (USB-powered) hobby circuits only. "
    "Use the provided tools to inspect inventory and board capabilities, then call propose_circuit for each "
    "candidate design. Do not invent components or pins. You cannot control hardware. When done, reply with a "
    "one-paragraph plain summary of tradeoffs. Never claim measurements you did not receive."
)


def extract_text(resp: dict) -> str:
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    out = []
    for item in resp.get("output", []):
        for part in item.get("content", []) or []:
            if part.get("type") in ("output_text", "text") and part.get("text"):
                out.append(part["text"])
    return "".join(out)


def extract_calls(resp: dict) -> list[dict]:
    return [i for i in resp.get("output", []) if i.get("type") == "function_call"]


def scene_to_components(detections: list[Detection]) -> list[ComponentInstance]:
    """Turn SAM detections into inventory. Electrical identity beyond segmentation is NOT inferred:
    resistances stay None (unknown) until the user confirms."""
    comps: list[ComponentInstance] = []
    for i, d in enumerate(detections):
        t = CONCEPT_TO_TYPE.get(d.concept, ComponentType.UNKNOWN)
        props: dict[str, Any] = {"resistance_ohm": None} if t == ComponentType.RESISTOR else ({"forward_v": None} if t == ComponentType.LED else {})
        comps.append(
            ComponentInstance(
                id=f"scan_{hashlib.sha1((d.concept + str(d.box)).encode()).hexdigest()[:8]}",
                type=t,
                name=f"{d.concept.title()} {i + 1}",
                bbox=BBox(x1=d.box[0], y1=d.box[1], x2=d.box[2], y2=d.box[3], img_w=d.img_w, img_h=d.img_h),
                mask_path=d.mask_path,
                mask_bounds=list(d.mask_bounds) if d.mask_bounds else None,
                pins=default_pins(t),
                properties=props,
                confidence=d.confidence,
                status=Provenance.UNCERTAIN if (d.confidence is not None and d.confidence < 0.6) else Provenance.DETECTED,
            )
        )
    return comps


class MetaReasoning:
    name = "meta-muse-spark"
    live = True
    MAX_ROUNDS = 5

    def __init__(self, settings: Settings, client: MetaClient | None = None, metrics: Any = None):
        self.s = settings
        self.metrics = metrics
        self.usage_meter = Usage(budget=settings.META_MAX_REQUESTS)
        self.client = client or MetaClient(settings, self.usage_meter, metrics, "muse")
        self.last_trace: list[dict] = []

    async def propose_designs(self, ctx: PlanContext) -> list[DesignCandidate]:
        tctx = ToolContext(ctx.inventory, ctx.board_id, ctx.goal, ctx.telemetry_summary)
        prompt = (
            f"Goal: {ctx.goal}\nBoard: {ctx.board_id}\nKnown failures: {ctx.failures or 'none'}\n"
            "Propose every feasible design for the goal using only available components. "
            "If a required component is unavailable, consider substitute sensing such as phone_camera_brightness "
            "and state clearly that it is a substitute."
        )
        input_: list[dict] | str = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}]
        prev_id: str | None = None
        trace: list[dict] = []
        t_start = time.monotonic()
        for _ in range(self.MAX_ROUNDS):
            body: dict[str, Any] = {"model": self.s.META_REASONING_MODEL, "instructions": SYSTEM_PROMPT, "input": input_, "tools": tool_specs(), "reasoning": {"effort": "medium"}}
            if prev_id:
                body["previous_response_id"] = prev_id  # server rebuilds history; send only the tool outputs
            resp = await self.client.json(body)
            prev_id = resp.get("id")
            calls = extract_calls(resp)
            trace.append({"round": len(trace) + 1, "tool_calls": [c.get("name") for c in calls], "text": extract_text(resp)[:1000], "usage": resp.get("usage")})
            if not calls:
                break
            if not prev_id:
                raise ProviderError("bad_response", "Response had no id; cannot continue the tool loop.")
            outs = []
            for c in calls:
                try:
                    args = json.loads(c.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = call_tool(tctx, c.get("name", ""), args)  # unknown tools are rejected here
                outs.append({"type": "function_call_output", "call_id": c.get("call_id"), "output": json.dumps(result)})
            input_ = outs
        if self.metrics:
            self.metrics.record("muse.design_total_ms", (time.monotonic() - t_start) * 1000, "ms", "live", rounds=len(trace))
        self.last_trace = trace + [{"tool_trace": tctx.trace}]
        if not tctx.candidates:
            raise ProviderError("no_valid_proposal", "Muse Spark produced no circuit proposal.", "Retry, or use the deterministic planner explicitly.")
        return list(tctx.candidates.values())

    async def interpret_scene(self, image: bytes, detections: list[Detection]) -> list[ComponentInstance]:
        return scene_to_components(detections)

    def usage(self) -> dict:
        return self.usage_meter.as_dict()
