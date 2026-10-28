# Architecture

```
Camera (laptop / phone) ──frame (explicit Scan)──▶ SAM 3.1 ──masks/boxes──▶ Digital twin ◀─ user corrections
                                                           │                      │
                                          Muse Spark ◀─ tools (read-only, whitelisted)
                                                           │ proposals
                                            Deterministic validator (boards, rules)
                                                           │ PASS/WARNING/BLOCKED/UNKNOWN
                                         Guided assembly (user confirms each step)
                                                           │ authorize (user)
                         HardwareSession ──NDJSON serial──▶ ESP32 firmware ──telemetry──▶ Verification
                                                           │ evidence ladder, contradictions
                                   Revision timeline ◀── inventory change ──▶ invalidate → redesign
```

| Layer | Code |
|---|---|
| A Perception | `apps/web` Workspace, `providers/meta.py` MetaSegmentation, `lib/geometry.ts` |
| B Twin | `models.py`, `twin.py`, `db.py` (SQLite JSON records) |
| C Intelligence | `providers/meta.py` MetaReasoning, `tools.py`, `designs.py` |
| D Validation | `validation.py`, `boards.py` |
| E Hardware | `hardware/*`, `verification.py`, `firmware/` |
| F UX | `apps/web/src/components/*` |

State machine: `workflow.TRANSITIONS`. Entering TEST requires an approved, validated design and confirmed steps. An inventory loss that breaks a design forces REDESIGN and revokes actuation.

## Data flow of the key demo moment
`PATCH /api/inventory/{pid}/{cid} {available:false}` → `Engine.update_component` → `_sync` snapshot + `diff_snapshots` → `invalidate_designs` (required components re-checked) → revision events, `ProjectState.invalidated`, `hw.revoke()`, stage REDESIGN → `POST /designs/{pid}/plan` → provider proposes → validator re-checks → user approves → assembly → authorize → phone brightness → `HardwareSession.set_led` (whitelisted, pin-checked) → `evaluate_phone_light`.

## Meta AI integration
- SAM 3.1: `POST /v1/responses` with `stream:true`, one concept per call, `metadata.mask_encoding=one_bit`, `include_confidence`. Output parsed by official `meta-sam-parser`; masks → SVG paths → overlays. Cache by image hash + concept; budget, throttle, retry/backoff.
- Muse Spark 1.3: tool-calling loop over `tools.py`; its `propose_circuit` output is built by trusted generators then validated. Decision trace = tool names/args/results only.
- Both unverified against live endpoints (no key yet).

## Dev/CI hygiene
Contracts regenerate with `scripts/gen_contracts.py`. Tests: pytest (unit, e2e via Engine, HTTP integration), vitest.
