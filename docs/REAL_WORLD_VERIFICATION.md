# Real-world verification log

Phase 2, started 2026-10-26. Everything below states what was actually run. Items without evidence are marked NOT DONE.

## Environment
Windows 11 Home, Python 3.11.9, Node 22.16, pnpm 11.6, PlatformIO Core (pip, venv) 6.2.0 after self-upgrade. 15.6 GB RAM, ~0.4 GB free during the session (Edge/Defender dominate); dev processes kept minimal. Serial ports enumerated (read-only, never opened): COM3, COM4, COM5, COM6, COM7, COM9; board identity unknown.

## 1. Meta Model API
| Check | Result |
|---|---|
| Docs read (quickstart, api-reference, tool-calling, structured-output, image-understanding, SAM segmenting/client-libraries) | DONE |
| Responses endpoint `POST /v1/responses`, bearer auth, models `muse-spark-1.3` / `sam-3.1` | matches docs |
| Tool definition format (flat `{type,name,description,parameters}`) | matches docs; asserted by test |
| Tool loop | CHANGED to docs-recommended `previous_response_id` + only `function_call_output` items (was re-sending full history); asserted by mocked-transport test |
| `reasoning.effort`, usage fields (`input_tokens`, `output_tokens`) | sent / recorded as metrics |
| Error envelope `{error:{type,message}}` | parsed into `ProviderError` (test) |
| Base64 `data:` image URLs in `input_image` | documented as supported (50 MB inline limit) |
| Structured output (`text.format` json_schema) | NOT used: designs are built by trusted code from tool calls, so it is not needed |
| SAM streaming + official `meta-sam-parser` | parser behavior verified locally with synthetic records only |
| **Any live request** | **NOT DONE. No `MODEL_API_KEY` was available.** |

Credential-dependent checkpoint (run once the key is in `.env`, never in chat):
```powershell
.\.venv\Scripts\python scripts\live_smoke.py --image path\to\real_bench.jpg --concept resistor
```
Writes `docs/demo/live_smoke_result.json` (ping, real design proposal + validation, latency, tokens) and `docs/demo/sam_overlay_live.png`. Then run the Real Mode project in the UI for the end-to-end pipeline, and `scripts/eval_sam.py` for accuracy.

### 1b. Open-weight route (investigated 2026-10-28)
Search of the Hugging Face Hub (read-only, via the connected account) found real Meta open-weight models:
- `meta-models/Muse-Glimmer-30B` (Apache-2.0, image-text-to-text). **Served live by Inference Providers: Together, Featherless AI, DeepInfra** (Hub metadata). This is the supported route for a real Meta model without the Meta Model API.
- `meta-models/Muse-Spark-Open-816B-A42B` (+NVFP4); too large for this laptop, no provider listed in the metadata I read.
- `facebook/sam3.1` (gated checkpoint; SAM 3.1 weights). The app has no local SAM runner; SAM 3.1 inference still needs the Meta Model API.
**Live attempt (2026-10-28, `.env` had `HF_TOKEN`; value never printed):** the router listed `meta-models/Muse-Glimmer-30B` with providers Featherless AI (live, no metadata) and DeepInfra (live, 131,072 ctx, tools: true, structured output: true, listed price input 0.3 / output 1.2 per 1M tokens). `scripts/live_smoke.py --backend open_weight` (pinned `:deepinfra`) got **HTTP 402: "You have no remaining credits"**. This is a **billing blocker on the Hugging Face account, not evidence the integration is wrong**: auth succeeded (the models listing returned 200) and the request reached the provider router. Result file: `docs/demo/live_smoke_result.json`. No credits will be purchased; the adapter now reports `payment_required` with the provider's label instead of "Meta API".

Attempts to call models without a token: community Spaces were not usable (not MCP-enabled / 503 / unvetted third-party code), so **no inference was run**. The adapter (`providers/open_weight.py`) targets `https://router.huggingface.co/v1` (OpenAI-compatible Chat Completions) and needs a token you create (Inference Providers permission) in `.env` as `OPEN_WEIGHT_API_KEY`. Tool calling support of Glimmer via each provider is unknown; the adapter falls back to a constrained JSON choice (closed enum) when the model does not call tools.
Status: adapter tested with a mock transport only. **Real inference: NOT DONE.** Run: `python scripts\live_smoke.py --backend open_weight` after configuring `.env`; it records model id, served model, latency, token usage (and an ESTIMATED cost only if you configure prices).

### 1c-i. Baseten model-identifier audit (2026-10-29)
- Hackathon guide slug (per the hackathon participant guide): `meta-models/muse-spark-open-1026`. Baseten's general docs/changelog: `meta-models/muse-spark-open`. No public Baseten page or web search result I could read lists the `-1026` slug, so it is **unconfirmed by documentation**.
- Third-party price/capability aggregators (not official, unverified) list Baseten Muse Spark Open at $1.25 / $4.25 per 1M input/output tokens with function calling and vision. Baseten's own docs say JSON mode/structured outputs are unsupported for Muse Spark Open, `n>1` returns 400, and the feature table (tool-calling/vision rows) did not render in the copy I could fetch.
- The authority is the account catalog: `scripts/baseten_audit.py` (read-only `GET /v1/models`) writes `docs/demo/baseten_audit_result.json`. **Not run: no `BASETEN_API_KEY` in `.env`** (only `HF_TOKEN` is set).
- Code: the preset default is now the guide's slug and is overridable with `OPEN_WEIGHT_MODEL`; HTTP 404 maps to `model_not_found` with a pointer to the audit and the request is never retried under another slug; if a provider rejects the `tools` field the adapter records `tools_rejected_by_provider` in the trace and switches to the constrained-JSON choice; `response_format`, `n`, and other unsupported fields are never sent. Tested with a mock transport (not Baseten).
- Image-plus-text proof is ready: `python scripts\live_smoke.py --backend open_weight --vlm-image C:\path	orame.jpg` records raw model output, latency, usage, model and provider. **Not run (no key/credits).**

### 1c. Baseten route (researched 2026-10-28, NOT called)
Baseten changelog + docs: model slug `meta-models/muse-spark-open`; endpoint `https://inference.baseten.co/v1/chat/completions` (OpenAI-compatible); `Authorization: Bearer <BASETEN_API_KEY>`; image input via `image_url` content parts, base64 accepted; JSON mode / structured outputs are **not** supported for Muse Spark Open and `n>1` returns HTTP 400; tool-calling support for this model is not stated; pricing not published in the pages I could read. Preset implemented (`BASETEN_API_KEY`, `OPEN_WEIGHT_PROVIDER=baseten|huggingface|custom|auto`); a test asserts the exact URL, auth header and that no `response_format`/`n` is sent. Slug and format are from documentation only; `GET /v1/models` was not called (needs a Baseten key). **Live availability: UNVERIFIED.**

### 1d. Camera → VLM pipeline (prepared)
`PERCEPTION_BACKEND=vlm` sends the preprocessed frame (≤1280 px, metadata-free JPEG, quality warnings) to the open-weight multimodal model and parses a strict JSON inventory (boxes are model estimates, **no masks, not SAM**). Verified with a mock transport and a clearly labelled SYNTHETIC fixture (`tests/fixtures/vlm/synthetic_vlm_response.json`). No real model has seen a real frame yet. The editable inventory (rename, type, values, confirm, remove, discard-false-detection) is browser-tested.

## 2. Firmware compile (actually run)
`cd firmware\esp32; pio run -j 2` (PlatformIO 6.2.0, espressif32@8.0.0, Arduino core, ArduinoJson ^7).
- First attempt failed: the 685 MB `toolchain-riscv32-esp` download dropped (`IncompleteRead`). Retried unchanged; second run succeeded.
- Result: **[SUCCESS] 756.62 s**. RAM 6.9 % (22,756 / 327,680 B), Flash 27.3 % (358,452 / 1,310,720 B). `firmware.bin` created.
- Compile only. **Not flashed, not run on hardware.** Pin map (LED GPIO25, ADC GPIO34) is for ESP32-DevKitC V4 only.
- Arduino Uno variant (`cd firmwarerduino; pio run -j 2`, atmelavr): **[SUCCESS] 377.77 s**, RAM 38.6 % (790 / 2048 B), flash 55.6 % (17,922 / 32,256 B). Compile only; SRAM headroom is thin for ArduinoJson, so runtime stability is unverified.
- Serial protocol tests run against the simulated adapter (`apps/api/tests/test_protocol_hardware.py`), not firmware.

## 3. Hardware readiness
- Read-only diagnostics endpoint/UI (`/api/hardware/diagnostics`, Hardware tab): lists ports without opening; test asserts no `serial.Serial` call. DONE.
- Serial connect now requires `approve_open_port=true` and `confirm_port` equal to the port; tested (400 `approval_required`). DONE.
- **Finding from the read-only enumeration:** COM3, COM4, COM5, COM6, COM7, COM9 all report "Standard Serial over Bluetooth link" with no USB VID/PID. None is a USB-serial board bridge, so **no ESP32/Arduino appears to be plugged in**. Plug in the board (data USB cable, CP210x/CH340 driver) and rescan.
- Re-check 2026-10-29: `enumerate_ports()` returns **no ports** (the Bluetooth COM3-9 seen earlier are gone) and a read-only PnP query finds no USB serial bridge (CP210x/CH340/CH9102/FTDI/Espressif) or Ports-class device. No ESP32 is attached. COM port approval, handshake and telemetry: NOT TESTED.
- First-validation checklist (`POST /api/hardware/validate`, Hardware tab button): handshake, protocol v1, board profile, heartbeat latency, telemetry rate, LED-off-at-boot; LED circuit and optical are listed as `pending`. It never actuates. Tested on the simulated device (flagged "NOT hardware validation") and against a fake firmware that boots with the LED high (check fails).
- No port was opened, nothing flashed, no command sent to hardware. Physical validation sequence: NOT DONE (awaiting board/port confirmation).

## 4. Optical verification
Backend `optical.py` (baseline, threshold, noise/drift → `uncertain`), `apply_optical` evidence merge, UI (drag LED region on live video, OFF→ON→OFF). Unit + HTTP tests pass. A simulated device can never exceed `sensor_response_observed`; optical alone reaches `optical_output_observed`, never `physical_behavior_verified`. Not exercised with a real camera/LED.

## 5. Metrics
`metrics.py` persists measured observations with provenance (`live|simulated|recorded`): SAM/Muse latency, tokens, validation outcomes, serial latency, redesign seconds, classification corrections, optical delta. `/api/metrics`, UI panel. Values so far come only from tests/demo (simulated). No live numbers exist yet. Cost is not estimated: pricing is not configured.

## 5b. Browser end-to-end (actually run)
`cd apps\web; npx playwright test` (installed Chrome, headless, 1 worker, own ports 8100/5183, temp DB): **4 passed in ~28 s**.
Covers: project creation, demo scan with overlay, unknown-resistance flag, inventory confirmation, design + validation display (estimate labels), assembly guidance, execution authorization on the simulated device, bright/dark test with simulated labelling, photoresistor removal, invalidation text, automatic REDESIGN, alternative design with substitute-sensor warning, phone-brightness readings (LED ON/OFF), before/after comparison, reload persistence; Real Mode shows no demo controls; hardware diagnostics read-only and Connect disabled without approval; screenshots saved to `docs/demo/screens/`.
Bugs the browser tests found (and fixed, with regression tests): `plan()` kept a stale project so the stage stayed PLAN and approval never reached GUIDE; fixed demo component ids collided across projects.
Unit/integration (re-run 2026-10-29): backend 100 passed + integration 5 passed = 105; vitest 12 passed; `tsc` clean; `ruff check apps/api scripts` clean; Playwright 5 passed.

## 5c. Agent control-loop audit (2026-10-28, `tests/test_agent_audit.py`, 8 tests)
Verified against the real revision log: the recorded stage path for scan → plan → approve → authorize → test → remove part → redesign → approve → authorize → test is exactly `OBSERVE→INTERPRET→CONFIRM→PLAN→VALIDATE→GUIDE→TEST→COMPARE→REDESIGN→VALIDATE→GUIDE→TEST→COMPARE`. Illegal jump (`TEST` from `OBSERVE`) returns 409 and writes nothing. Unknown resistance blocks approval and the agent names it. When every candidate is BLOCKED the agent asks for fixes and approval stays refused. Authorization gates fire in order: `no_design` → `assembly_unconfirmed` → `steps_out_of_order` → `hardware_unavailable` → (ok); start/phone/optical commands return `not_authorized` before authorization and after emergency stop. A non-approved LED pin is refused by the session. Model detections stay `detected/uncertain` until the user confirms; a type correction is metered.
Bugs found by this period's tests: blank numeric keys copied from `.env.example` crashed `Settings` (fixed, regression test); chat-format token usage keys (`prompt_tokens`) were recorded as 0 (fixed); metric summaries rounded tiny USD estimates to 0 (fixed).
Phone: `e2e/phone.spec.ts` uses Chrome's SYNTHETIC camera feed (real getUserMedia frames, not a phone). Verified: frame capture, JPEG upload (>2 kB), connection indicator, calibration guard (uncalibrated until two distinct references), phone-triggered scan marks all components detected/uncertain (none user-confirmed). Real phone over HTTPS: NOT DONE.

## 6. Stability
`scripts/start.ps1` / `stop.ps1`: PID file, duplicate and foreign-port refusal, child-tree kill of recorded PIDs only. Playwright uses its own ports (8100/5183), one worker, installed Chrome, headless.

## 7. Prior art (web search, 2026-10-27, standard mode)
No existing project found that combines camera component recognition, validated design, guided build and hardware-verified feedback. Neighbours: Roboflow Universe breadboard/PCB detector datasets (detection only), Flux.ai Copilot (schematic edits with user approval, PCB-focused), Fritzing (breadboard view, no AI). One search round only; this is not an exhaustive survey.

## Remaining blockers
Meta key; a board physically connected over USB (none detected); confirmed board + COM port + approval to open it; flashed firmware; real bench photos; real phone test over HTTPS.
