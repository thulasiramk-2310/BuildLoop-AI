# Demo checklist

## Before (Demo Mode, no hardware, ~2 min)
- [ ] `scripts\dev.ps1` running; header shows `DEMO MODE · simulated`.
- [ ] New Demo project → Load synthetic demo frame → Scan components (6 components).
- [ ] Enter resistances (A=220 Ω, B=10000 Ω) and LED Vf=2.0 V; Confirm all.
- [ ] Copilot → Design: both designs PASS; approve photoresistor design; confirm 4 steps.
- [ ] Hardware → Use simulated device → tick approval → Authorize → run bright/dark phases → Evaluate. Ladder tops at "sensor response observed" with `simulated` badge.
- [ ] Inventory → Remove photoresistor. Stage jumps to REDESIGN; design shows INVALID.
- [ ] Propose redesign → phone-camera design → approve → confirm steps → authorize → send readings → Evaluate.
- [ ] Design tab shows before/after and limitations.

## Real Mode (needs key + board)
- [ ] `.env` has `MODEL_API_KEY`; header shows `Meta API key set`.
- [ ] `python scripts/diagnose.py` lists the board's COM port.
- [ ] Firmware built and flashed by you (docs/hardware/SETUP.md); Hardware tab handshake shows `buildloop-esp32`.
- [ ] Wiring matches plan; every step confirmed; LED lights when sensor covered.
- [ ] Phone paired over https; brightness calibrated dark/bright.
- [ ] Record observations honestly; do not present simulated results as physical.

## Failure drills
- [ ] Unplug USB during test → status `lost`, LED goes off (firmware host-timeout 10 s).
- [ ] Deny camera permission → clear message.
- [ ] Start Real project without key → `no_api_key` error with hint.
