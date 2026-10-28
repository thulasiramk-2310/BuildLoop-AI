# Known limitations (honest list)

- **No live Meta call has been made.** SAM parsing is tested with synthetic records built by the parser's own encoder, not recorded API responses. Muse Spark tool definitions and the `previous_response_id` tool loop match the official docs (read 2026-10-26) and a mock transport, not the live service. Base64 data URLs for images are documented as supported.
- **ESP32 firmware compiles (PlatformIO, 2026-10-27) but is not flashed or run.** The fake device implements the same protocol and is what tests use. The Uno variant also compiles (SRAM 38.6 %); not run.
- **No physical results.** All telemetry in tests/demo is simulated and labelled; verification never exceeds "sensor response observed" in that case.
- **Optical verification** has a backend evaluator, tests and a UI (drag LED region on live video, OFF→ON→OFF); never exercised with a real camera and LED. It reports visible change only.
- Real Mode scan uses SAM only for detection; Muse Spark does not yet read resistor bands or refine classes (resistance stays UNKNOWN by design).
- Perspective correction, alignment markers and camera calibration are not implemented. ROI crop is.
- Component tracking between frames (OpenCV) is not implemented; each scan replaces identities by content hash.
- Digital twin shows planned connections only; wire detection is out of scope.
- Only ESP32-DevKitC V4 and Uno profiles; only the adaptive-light designs.
- Playwright e2e covers Demo Mode (4 tests pass); no accessibility audit, no real-phone test, no live-Meta e2e.
- **Hugging Face live call blocked by billing (HTTP 402, 2026-10-28).** Baseten preset is documented-only. No real Meta open-weight model has processed a BuildLoop input yet.
- Camera→VLM inventory has only been exercised with mock/synthetic fixtures; model boxes (when returned) are estimates, there are no masks, and SAM 3.1 is not involved in that path.
- **Open-weight backend** (`REASONING_BACKEND=open_weight`) is implemented and tested against a mock transport only. No partner endpoint has been called. `meta-models/Muse-Glimmer-30B` is listed as served by Together/Featherless/DeepInfra on the Hugging Face router; tool-calling support there is unverified (constrained-JSON fallback exists). Its output is labelled `open-weight:<model>@<host>` and is never presented as Meta Model API / Muse Spark 1.3. SAM 3.1 has no open-weight route in this app.
- The agent control loop (`agent.py`) is a deterministic policy over the state machine plus model-proposed designs. It is not a free-form LLM planner; replan decisions are rule-based and visible.
- Frontend telemetry WS and phone WS have no auth; intended for a trusted LAN.
