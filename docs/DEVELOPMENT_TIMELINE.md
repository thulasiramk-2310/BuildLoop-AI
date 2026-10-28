# Development timeline

This page states plainly when each part of BuildLoop AI was built, for the Meta Global AI Developer Hackathon 2026
(Indie Developer track; building period October 27 – November 4, 2026).

**About the git history.** The project was developed in a local working tree before it had any commits. This repository's
history is a single snapshot created when the code was published, so commit dates do **not** show when features were
written. The dates below come from the development log and file timestamps.

## Before the building period (October 26, 2026): initial prototype

Demo Mode and the core engineering stack, verified only against simulated hardware:

- FastAPI backend, SQLite persistence, typed contracts, REST and WebSocket APIs
- Reality-synced digital twin: inventory, snapshots, diffs, design invalidation, revision timeline
- Deterministic electrical validation (board pin database, ADC1 / input-only pins, 3.3 V logic, LED current, voltage divider)
- Stage state machine, guided assembly, approval and authorization gates
- Serial protocol, simulated ESP32 device, ESP32 and Arduino Uno firmware source
- Meta SAM 3.1 and Muse Spark provider code (never run against the live service)
- React workstation UI, Playwright end-to-end tests

## During the building period (October 27 onward)

- Agent control loop: next action, blockers, replan triggers (`/api/agent/{project}`)
- Alternative reasoning backend for Meta open-weight models (Hugging Face router, Baseten), labelled and opt-in
- Camera-frame preprocessing and quality checks; vision-language inventory path with a strict parser
- Editable inventory (rename, discard false detections); manual-override labelling of part removal
- Non-actuating first-validation checklist for a physical ESP32 session
- Baseten model-catalog audit script; payment, model-not-found and tools-rejected handling
- Agent-loop audit tests, phone-capture end-to-end test, evaluation and smoke-test scripts
- Documentation and verification log

What has and has not been verified is recorded in [REAL_WORLD_VERIFICATION.md](REAL_WORLD_VERIFICATION.md) and
[demo/KNOWN_LIMITATIONS.md](demo/KNOWN_LIMITATIONS.md).
