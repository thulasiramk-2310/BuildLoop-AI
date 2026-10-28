# SAM evaluation dataset (currently EMPTY)

No images are committed yet. Do not add anyone's photo without their consent. Add only your own bench photos (or CC0 images) and record that in the manifest.

1. Put images here (JPEG/PNG), e.g. `bench1.jpg` (aim for 10–20 varied shots: lighting, angle, clutter).
2. Hand-label boxes in original pixel coordinates for the concepts BuildLoop queries: `esp32 development board`, `breadboard`, `led`, `resistor`, `photoresistor`, `pushbutton`, `jumper wire`.
3. Create `manifest.json`:
```json
{"images": [{"file": "bench1.jpg", "consent": "own photo, 2026-10-27", "license": "CC0",
  "boxes": [{"concept": "led", "x1": 410, "y1": 220, "x2": 455, "y2": 290}]}]}
```
4. `python scripts/eval_sam.py --max-calls 10` (spends API credits) writes `docs/demo/sam_eval_result.json` with precision/recall at IoU ≥ 0.5 and measured latency, provenance `live`.

Segmentation only locates objects. Electrical identity (resistance, LED colour) is never scored here because SAM does not provide it.
