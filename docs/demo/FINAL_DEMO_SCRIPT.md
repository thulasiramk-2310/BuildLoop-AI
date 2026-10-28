# Final 3-minute demo script

Rule: record only what truly works at recording time. Every segment lists its **evidence label** and a **fallback that is still honest** (never a fake). If a live step fails, show the failure state or the labelled Demo Mode, and say so on screen.

Evidence labels: LIVE-META · REAL-HW · REAL-CAMERA · SIMULATED · RECORDED.

| Time | Show | Say | Evidence required / honest fallback |
|---|---|---|---|
| 0:00–0:20 | Real bench: ESP32, LED, resistors, photoresistor. A design drawn for parts you do not have. | "AI can draw circuits, but it cannot see your bench, or notice when a part disappears, or check what actually happened." | REAL-CAMERA. Fallback: still photo of the bench, stated as a photo |
| 0:20–0:45 | Live camera frame → Scan → SAM masks over real parts → inventory; correct one detection; enter resistor value | "SAM 3.1 finds the objects. It cannot know resistance, so BuildLoop marks it unknown until I confirm." | LIVE-META + REAL-CAMERA. Fallback: show the explicit `no_api_key` error, then Demo Mode with its SIMULATED badge |
| 0:45–1:10 | Copilot → Design; decision trace listing tool calls; two candidate designs | "Muse Spark plans through a closed tool set. It proposes; trusted code builds." | LIVE-META. Show model id, latency and tokens from the Measurements panel |
| 1:10–1:35 | Validation results (PASS/WARNING/BLOCKED with rule names); assembly step confirm; authorize; ESP32 telemetry, LED on/off | "Deterministic rules decide, not the model. Hardware only moves after my authorization." | REAL-HW. Fallback: simulated device with SIMULATED badge visible |
| 1:35–2:00 | Pull the photoresistor out; mark it removed; design turns INVALID; stage → REDESIGN; agent banner explains why | "Reality changed, so the design is no longer true. BuildLoop noticed and stopped trusting it." | Real removal on camera |
| 2:00–2:30 | Propose redesign (phone-camera brightness); read the substitute-sensor warning; approve; phone streams brightness; LED follows | "It is a substitute sensor: the phone sees the room, not the LED. The app says so." | LIVE-META for the proposal; REAL-HW for LED |
| 2:30–2:50 | Optical check on the LED region; evidence ladder; before/after panel | "Command acknowledged is not LED lit. The camera's visible-change check is the independent evidence." | REAL-CAMERA + REAL-HW. If the check is uncertain, show the uncertainty |
| 2:50–3:00 | Metrics table with provenance; one-line summary; baseline vs new work | "Observe, design, validate, adapt, verify — measured, with every number labelled." | Only measured numbers |

On-screen caption near the end: "Pre-existing baseline (before Oct 27): Demo Mode prototype. Built during the event: <list from DEVELOPMENT_TIMELINE.md>."

Do not: show recorded results as live, hide the simulated badge, cut around failures, or claim physical verification without optical or sensor evidence.
