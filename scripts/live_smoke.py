"""Smallest useful live Meta API smoke test. Needs MODEL_API_KEY in .env (never printed).

Calls made (default): 1 Muse Spark ping + 1 Muse Spark tool-loop design (usually 2-3 requests) + 1 SAM request per
--concept (default: one). Writes docs/demo/live_smoke_result.json and, with --image, an overlay PNG from the REAL parsed
SAM output. Usage:  python scripts/live_smoke.py --image path/to/bench.jpg --concept resistor
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import cv2
import numpy as np
from buildloop.config import get_settings
from buildloop.metrics import Metrics
from buildloop.models import Provenance
from buildloop.providers.base import PlanContext, ProviderError
from buildloop.providers.demo import demo_components
from buildloop.providers.meta import MetaReasoning, MetaSegmentation
from buildloop.providers.open_weight import OpenWeightReasoning
from buildloop.validation import validate_design


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path, help="real photo for SAM")
    ap.add_argument("--concept", action="append", help="SAM concept (repeatable, one request each)")
    ap.add_argument("--skip-muse", action="store_true")
    ap.add_argument("--vlm-image", type=Path, help="real camera frame for the open-weight image+text request (no SAM)")
    ap.add_argument("--backend", choices=["meta_api", "open_weight"], default=None, help="reasoning backend (default: REASONING_BACKEND from .env)")
    args = ap.parse_args()

    s = get_settings()
    backend = args.backend or s.REASONING_BACKEND
    need_meta = (backend == "meta_api" and not args.skip_muse) or bool(args.image)
    if need_meta and not s.meta_configured:
        print("MODEL_API_KEY is not set (needed for Meta Model API calls). Add it to .env locally, never in chat.")
        if not (backend == "open_weight" and s.open_weight_configured and not args.image):
            return 2
    if backend == "open_weight" and not s.open_weight_configured and not args.skip_muse:
        print("Open-weight backend not configured: set HF_TOKEN or BASETEN_API_KEY (or OPEN_WEIGHT_*) in .env.")
        return 2
    metrics = Metrics()
    result: dict = {"at": datetime.now(UTC).isoformat(), "reasoning_backend": backend, "models": {"segmentation": s.META_SEGMENTATION_MODEL}}
    if backend == "open_weight":
        result["models"]["reasoning"] = f"{s.ow_model} (open-weight via partner endpoint; NOT the Meta Model API)"
        result["base_url"] = s.ow_base_url
    else:
        result["models"]["reasoning"] = s.META_REASONING_MODEL
        result["base_url"] = s.META_BASE_URL

    if not args.skip_muse:
        mu = OpenWeightReasoning(s, metrics=metrics) if backend == "open_weight" else MetaReasoning(s, metrics=metrics)
        # 1) ping: proves auth, endpoint, model id, response parsing, usage fields
        t0 = time.monotonic()
        try:
            if backend == "open_weight":
                resp = await mu.client.json({"model": s.ow_model, "messages": [{"role": "user", "content": "Reply with the single word OK."}]})
                text = ((resp.get("choices") or [{}])[0].get("message", {}).get("content")) or ""
            else:
                from buildloop.providers.meta import extract_text

                resp = await mu.client.json({"model": s.META_REASONING_MODEL, "input": "Reply with the single word OK."})
                text = extract_text(resp)
            result["muse_ping"] = {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000), "text": text[:80], "usage": resp.get("usage"), "response_keys": sorted(resp), "served_model": resp.get("model")}
        except ProviderError as e:
            result["muse_ping"] = {"ok": False, "error": f"{e.code}: {e.message}"}
        # 2) real tool-calling design proposal, then deterministic validation
        t0 = time.monotonic()
        inv = demo_components()
        for c in inv:
            c.status = Provenance.USER_CONFIRMED
            if c.type.value == "resistor":
                c.properties["resistance_ohm"] = 220.0 if c.name.endswith("A") else 10000.0
            if c.type.value == "led":
                c.properties["forward_v"] = 2.0
        try:
            designs = await mu.propose_designs(PlanContext(goal="Build a light that turns on when the environment gets dark.", board_id="esp32-devkitc-v4", inventory=inv))
            result["muse_design"] = {
                "ok": True,
                "latency_ms": round((time.monotonic() - t0) * 1000),
                "designs": [{"title": d.title, "source": d.source, "validation": [{"rule": v.rule, "status": v.status.value} for v in validate_design(d, inv)]} for d in designs],
                "trace": mu.last_trace,
            }
        except ProviderError as e:
            result["muse_design"] = {"ok": False, "error": f"{e.code}: {e.message}", "trace": mu.last_trace}

    if args.vlm_image:
        if not s.open_weight_configured:
            print("Open-weight backend not configured; skipping the image+text request.")
        else:
            from buildloop.imaging import preprocess
            from buildloop.providers.open_weight import VlmPerception

            frame, rep = preprocess(args.vlm_image.read_bytes())
            vp = VlmPerception(OpenWeightReasoning(s, metrics=metrics))
            t0 = time.monotonic()
            try:
                comps = await vp.scan_scene(frame)
                result["vlm_scan"] = {
                    "ok": True, "latency_ms": round((time.monotonic() - t0) * 1000), "model": s.ow_model, "provider": s.ow_provider,
                    "frame": rep.as_dict(), "raw_model_output": vp.last_raw[:4000],
                    "components": [{"type": c.type.value, "name": c.name, "confidence": c.confidence, "status": c.status.value, "box": c.bbox.model_dump() if c.bbox else None} for c in comps],
                }
            except ProviderError as e:
                result["vlm_scan"] = {"ok": False, "error": f"{e.code}: {e.message}", "model": s.ow_model, "provider": s.ow_provider}

    if args.image:
        img = args.image.read_bytes()
        seg = MetaSegmentation(s, metrics=metrics)
        concepts = args.concept or ["resistor"]
        out = []
        canvas = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
        for c in concepts:
            t0 = time.monotonic()
            try:
                dets = await seg.segment(img, c)
                out.append({"concept": c, "ok": True, "latency_ms": round((time.monotonic() - t0) * 1000), "count": len(dets), "detections": [{"box": list(d.box), "confidence": d.confidence, "has_mask": bool(d.mask_path), "mask_bounds": d.mask_bounds} for d in dets]})
                for d in dets:
                    x1, y1, x2, y2 = (int(v) for v in d.box)
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 200, 0), 2)
                    cv2.putText(canvas, f"{c} {d.confidence or 0:.2f}", (x1, max(14, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)
            except ProviderError as e:
                out.append({"concept": c, "ok": False, "error": f"{e.code}: {e.message}"})
        result["sam"] = out
        dest = ROOT / "docs" / "demo" / "sam_overlay_live.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), canvas)
        result["sam_overlay"] = str(dest.relative_to(ROOT))

    result["metrics"] = metrics.summary()
    dest = ROOT / "docs" / "demo" / "live_smoke_result.json"
    dest.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: (v if k in ("muse_ping",) else "see file") for k, v in result.items() if k.startswith("muse") or k == "sam"}, indent=2, default=str))
    print(f"wrote {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
