"""Evaluate live SAM 3.1 detections against hand-labelled boxes. Needs MODEL_API_KEY. Spends credits: capped by --max-calls.

Manifest: tests/fixtures/eval/manifest.json
  {"images": [{"file": "bench1.jpg", "consent": "own photo", "license": "CC0",
               "boxes": [{"concept": "resistor", "x1": 10, "y1": 20, "x2": 80, "y2": 60}]}]}
Metric: a detection matches a ground-truth box of the same concept at IoU >= 0.5. Results are recorded with provenance
"live" and printed. No result is invented: an empty manifest produces no numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from buildloop.config import get_settings
from buildloop.metrics import Metrics
from buildloop.providers.base import ProviderError
from buildloop.providers.meta import MetaSegmentation

EVAL = ROOT / "tests" / "fixtures" / "eval"


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=10)
    args = ap.parse_args()
    manifest = EVAL / "manifest.json"
    if not manifest.exists():
        print("No manifest.json. See tests/fixtures/eval/README.md. Nothing evaluated.")
        return 1
    items = json.loads(manifest.read_text(encoding="utf-8")).get("images", [])
    if not items:
        print("Manifest has no images. Nothing evaluated.")
        return 1
    s = get_settings()
    if not s.meta_configured:
        print("MODEL_API_KEY is not set (put it in .env locally).")
        return 2
    metrics = Metrics()
    seg = MetaSegmentation(s, metrics=metrics)
    tp = fp = fn = calls = 0
    per_image = []
    for it in items:
        img = (EVAL / it["file"]).read_bytes()
        for concept in sorted({b["concept"] for b in it["boxes"]}):
            if calls >= args.max_calls:
                break
            calls += 1
            gt = [(b["x1"], b["y1"], b["x2"], b["y2"]) for b in it["boxes"] if b["concept"] == concept]
            try:
                dets = await seg.segment(img, concept)
            except ProviderError as e:
                per_image.append({"file": it["file"], "concept": concept, "error": f"{e.code}: {e.message}"})
                continue
            used: set[int] = set()
            hit = 0
            for d in dets:
                m = next((i for i, g in enumerate(gt) if i not in used and iou(d.box, g) >= 0.5), None)
                if m is None:
                    fp += 1
                else:
                    used.add(m)
                    hit += 1
            tp += hit
            fn += len(gt) - hit
            per_image.append({"file": it["file"], "concept": concept, "gt": len(gt), "detected": len(dets), "matched": hit})
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    out = {"calls": calls, "tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "per_image": per_image, "latency": metrics.summary()}
    (ROOT / "docs" / "demo" / "sam_eval_result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "per_image"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
