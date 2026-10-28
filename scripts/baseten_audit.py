"""Audit Baseten's model catalog for the hackathon Muse Spark Open slug. Read-only (GET /v1/models); needs BASETEN_API_KEY in .env.

Reports whether the configured slug exists for THIS account and what the catalog says about it. It changes nothing and
never prints the key. Output: docs/demo/baseten_audit_result.json
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from buildloop.config import Settings

CANDIDATES = ["meta-models/muse-spark-open-1026", "meta-models/muse-spark-open"]


def main() -> int:
    s = Settings()
    key = s.BASETEN_API_KEY.strip()
    if not key:
        print("BASETEN_API_KEY is not set in .env (create it yourself; never paste it into chat). Nothing audited.")
        return 2
    base = "https://inference.baseten.co/v1"
    configured = s.OPEN_WEIGHT_MODEL.strip() or CANDIDATES[0]
    r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    out: dict = {"at": datetime.now(UTC).isoformat(), "provider": "baseten", "base_url": base, "status": r.status_code, "configured_model": configured}
    if r.status_code != 200:
        out["error"] = r.text[:300]
        print(f"GET /v1/models -> {r.status_code}: {out['error']}")
    else:
        data = r.json().get("data", [])
        ids = [m.get("id", "") for m in data]
        out["models_total"] = len(data)
        out["muse_models"] = [m for m in data if "muse" in m.get("id", "").lower()]
        out["configured_model_listed"] = configured in ids
        out["candidates_listed"] = {c: (c in ids) for c in CANDIDATES}
        listed = [c for c in CANDIDATES if c in ids]
        out["recommendation"] = (
            f"Set OPEN_WEIGHT_MODEL={listed[0]}" if listed else "No Muse Spark Open slug is listed for this account. Check the hackathon credit/access instructions; do not guess a slug."
        )
        print(json.dumps({k: v for k, v in out.items() if k != "muse_models"}, indent=1))
        for m in out["muse_models"]:
            print("catalog entry:", json.dumps(m)[:500])
    dest = ROOT / "docs" / "demo" / "baseten_audit_result.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}")
    return 0 if out.get("status") == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
