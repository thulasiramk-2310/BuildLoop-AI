# Roadmap and status

Legend: **Verified** = run and passing here. **Simulated** = verified only against the simulated device / mock transport.
**Unverified** = implemented, never exercised against the real service or hardware.

## Component status

| Area | Status |
|---|---|
| Backend (FastAPI, SQLite, typed contracts), digital twin, deterministic validator, stage state machine, agent loop | Verified (unit + HTTP integration tests) |
| Serial protocol, session, heartbeat, authorization gates | Verified against the simulated device |
| ESP32 and Arduino Uno firmware | Compile verified (PlatformIO). **Never flashed or run** |
| Demo Mode end-to-end (scan → design → build → test → remove part → redesign → verify) | Verified in a real browser (Playwright) with a simulated device |
| Phone camera page, frame preprocessing, editable inventory | Verified with a synthetic camera feed; a real phone over HTTPS is untested |
| Meta SAM 3.1 / Muse Spark (Meta Model API) | Unverified: no live request has succeeded |
| Open-weight backends (Hugging Face router, Baseten) | Mock-transport tests only. Hugging Face returned HTTP 402 (no credits); Baseten catalog not audited |
| Vision-language inventory from a real camera frame | Unverified (fixture-tested only) |
| Optical LED check, physical ESP32 verification | Unverified |

## Next milestones

1. One real Meta-model request: `scripts/baseten_audit.py`, then `scripts/live_smoke.py --backend open_weight --vlm-image <frame>`.
2. Connect an ESP32, approve its serial port, run the first-validation checklist, then the LED test with an optical check.
3. Real closed loop with provenance per step; presentation-focused UI for the final demo.
4. Evaluation on a documented, consented image set (`scripts/eval_sam.py`).

## Known risks

- Provider behaviour (tool calling, JSON mode, image input) differs; unsupported fields are never sent and downgrades are recorded.
- SAM-style detection does not identify electrical values; resistor values and LED colour stay unknown until confirmed.
- Opening a serial port can reset an ESP32; the app waits for the boot and requires explicit approval.
- Phone camera access needs HTTPS (a self-signed certificate is used in development).
