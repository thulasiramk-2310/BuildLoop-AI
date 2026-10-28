"""Camera-frame preprocessing and quality checks before any frame leaves the machine.

Resizing bounds model cost/latency; re-encoding strips metadata (EXIF/GPS) from phone photos; quality warnings tell the
user a frame is likely unusable (blurry, dark, blown out) BEFORE spending an inference request. Nothing here changes
what is in the scene: no filters that could invent or hide parts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

MAX_SIDE = 1280
JPEG_QUALITY = 85
MIN_SIDE = 160
BLUR_VAR_MIN = 60.0  # variance of the Laplacian below this => likely out of focus / motion blur
DARK_LUMA = 0.12
BRIGHT_LUMA = 0.92


class ImageError(ValueError):
    pass


@dataclass
class FrameReport:
    width: int
    height: int
    orig_width: int
    orig_height: int
    resized: bool
    bytes_in: int
    bytes_out: int
    mean_luma: float
    sharpness: float
    warnings: list[str] = field(default_factory=list)
    usable: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def preprocess(data: bytes, max_side: int = MAX_SIDE) -> tuple[bytes, FrameReport]:
    """Decode (EXIF orientation applied by OpenCV), downscale to `max_side`, re-encode as JPEG, assess quality."""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ImageError("Image could not be decoded (expected JPEG/PNG/WebP).")
    h0, w0 = arr.shape[:2]
    if min(h0, w0) < MIN_SIDE:
        raise ImageError(f"Image is too small ({w0}x{h0}); need at least {MIN_SIDE}px on the short side.")
    resized = False
    scale = max_side / max(h0, w0)
    if scale < 1.0:
        arr = cv2.resize(arr, (round(w0 * scale), round(h0 * scale)), interpolation=cv2.INTER_AREA)
        resized = True
    h, w = arr.shape[:2]
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    luma = float(gray.mean() / 255.0)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings: list[str] = []
    if sharp < BLUR_VAR_MIN:
        warnings.append(f"Frame looks blurry (sharpness {sharp:.0f} < {BLUR_VAR_MIN:.0f}). Hold steady, tap to focus, retake.")
    if luma < DARK_LUMA:
        warnings.append(f"Frame is very dark (mean brightness {luma:.2f}). Add light.")
    if luma > BRIGHT_LUMA:
        warnings.append(f"Frame is washed out (mean brightness {luma:.2f}). Reduce glare/exposure.")
    ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ImageError("Could not re-encode the image.")
    out = buf.tobytes()
    rep = FrameReport(width=w, height=h, orig_width=w0, orig_height=h0, resized=resized, bytes_in=len(data), bytes_out=len(out), mean_luma=round(luma, 3), sharpness=round(sharp, 1), warnings=warnings, usable=not warnings)
    return out, rep
