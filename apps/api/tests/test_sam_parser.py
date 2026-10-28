"""SAM parsing against recorded-shape events. See tests/fixtures/sam_*.json."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from buildloop.config import Settings
from buildloop.providers.base import ProviderError
from buildloop.providers.meta import MetaSegmentation, parse_sam_events

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


LANE = {"item_id": "msg_1", "output_index": 0, "content_index": 0}


def event(delta: str):
    return {"type": "response.output_text.delta", "delta": delta, **LANE}


def done(text: str = ""):
    """Closes the lane then completes. `text` must equal the concatenated deltas."""
    return [{"type": "response.output_text.done", "text": text, **LANE}, {"type": "response.completed", "response": {"status": "completed"}}]


def stream(text: str = ""):
    return ([event(text)] if text else []) + done(text)


async def test_zero_detections_is_normal():
    dets = await parse_sam_events(stream(), 640, 480, "led")
    assert dets == []


def record(i: int, box: tuple[int, int, int, int], conf: float | None = None, w=640, h=480) -> str:
    """Synthetic SAM record: box + lossless mask built with the parser's own encoder (not a recorded API response)."""
    from meta_sam_parser._mask_codec import _encode_raster

    mw, mh = box[2] - box[0] + 1, box[3] - box[1] + 1
    payload = _encode_raster([1] * (mw * mh), mw, mh)
    c = f";c={conf}" if conf is not None else ""
    return f"<{i and 0}f>{i}<|box;x1={box[0]};y1={box[1]};x2={box[2]};y2={box[3]};w={w};h={h}{c}|><|mask;x=0;y=0;data={mh},{mw},{payload}{c}|>"


async def test_box_and_mask_parsed_with_confidence():
    dets = await parse_sam_events(stream(record(0, (10, 20, 49, 59), 0.91)), 640, 480, "led")
    assert len(dets) == 1
    assert dets[0].box == (10, 20, 50, 60)  # parser converts inclusive x2,y2 to half-open
    assert dets[0].confidence == pytest.approx(0.91, abs=0.01)
    assert dets[0].mask_path and dets[0].mask_path.startswith("M")


async def test_malformed_record_is_skipped_not_fatal():
    dets = await parse_sam_events(stream("<0f>0<|box;x1=1|>"), 640, 480, "led")
    assert dets == []


async def test_multiple_objects_on_one_line():
    line = "<0f>" + record(0, (1, 2, 20, 30))[4:] + "," + record(1, (100, 100, 130, 140))[4:]
    dets = await parse_sam_events(stream(line), 640, 480, "resistor")
    assert len(dets) == 2


def jpeg() -> bytes:
    _ok, buf = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))
    return buf.tobytes()


async def test_no_api_key_raises_clear_error():
    seg = MetaSegmentation(Settings(_env_file=None, MODEL_API_KEY=""))
    with pytest.raises(ProviderError) as e:
        await seg.segment(jpeg(), "led")
    assert e.value.code == "no_api_key"


async def test_budget_exhausted():
    s = Settings(_env_file=None, MODEL_API_KEY="k", META_MAX_REQUESTS=0)
    seg = MetaSegmentation(s)
    with pytest.raises(ProviderError) as e:
        await seg.segment(jpeg(), "led")
    assert e.value.code == "budget_exhausted"


async def test_invalid_key_maps_to_auth_error(monkeypatch):
    import httpx

    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    monkeypatch.setattr("buildloop.providers.meta.httpx.AsyncClient", Patched)
    seg = MetaSegmentation(Settings(_env_file=None, MODEL_API_KEY="bad", META_MIN_INTERVAL_S=0))
    with pytest.raises(ProviderError) as e:
        await seg.segment(jpeg(), "led")
    assert e.value.code == "auth_invalid"


async def test_cache_hit_avoids_second_call(monkeypatch):
    seg = MetaSegmentation(Settings(_env_file=None, MODEL_API_KEY="k", META_MIN_INTERVAL_S=0))
    calls = {"n": 0}

    async def fake_sse(body):
        calls["n"] += 1
        return stream(record(0, (1, 2, 30, 40), w=64, h=48))

    monkeypatch.setattr(seg.client, "sse", fake_sse)
    img = jpeg()
    a = await seg.segment(img, "led")
    b = await seg.segment(img, "led")
    assert calls["n"] == 1 and a == b and seg.usage()["cache_hits"] == 1


def test_fixture_dir_exists():
    assert FIX.parent.exists()
