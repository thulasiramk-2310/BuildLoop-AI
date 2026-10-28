# Test report (actual runs, 2026-10-26/27, Windows 11, Python 3.11.9, Node 22.16)

| Suite | Command | Result |
|---|---|---|
| Backend unit + Engine e2e | `python -m pytest apps/api -q` | 100 passed |
| HTTP integration (REST + phone WebSocket, Demo Mode, simulated device) | `python -m pytest tests/integration -c apps/api/pyproject.toml --rootdir . -q` | 5 passed |
| Frontend unit (geometry, brightness) | `npx vitest run` | 12 passed |
| Frontend typecheck | `npx tsc -b --noEmit` | clean |
| Backend lint | `ruff check apps/api` | All checks passed |
| Browser e2e (Playwright, Chrome headless) | `cd apps/web; npx playwright test` | 5 passed (~33 s): full Demo Mode loop incl. removal → invalidation → redesign → new verification record, Real Mode has no demo controls, diagnostics read-only, screenshots, phone page with synthetic camera |
| ESP32 firmware compile | `pio run -j 2` in firmware/esp32 | SUCCESS, RAM 6.9 %, flash 27.3 % (compile only) |
| Arduino Uno firmware compile | `pio run -j 2` in firmware/arduino | SUCCESS, RAM 38.6 %, flash 55.6 % (compile only) |
| Browser smoke (Chrome, Vite http) | manual via automation | app renders; Demo project, synthetic frame, scan gives 6 components with overlay; Design tab shows validation; twin renders |

## Spec test-scenario coverage
Covered by automated tests: no API key (1), invalid key (2), zero SAM objects (4), multiple objects (5), user corrects detection (7), unsupported board (8, firmware handshake + validator), disconnect during telemetry (9), missing resistor (10), pin conflict (12), missing photoresistor triggers redesign (13), user rejects design (15), command timeout (16), contradictory evidence (17), restart preserves history (18), demo vs real separation (19), unauthorized model tool call rejected (20), invalid voltage (11, 5V to GPIO).
Not covered: no camera permission (UI path implemented, untested), incorrect classification (3, only via the manual type-edit UI), redesign failing validation (14, partially: BLOCKED designs are filtered, no dedicated test).

## NOT run / NOT verified
- Any live Meta SAM 3.1 or Muse Spark request (no API key).
- Any physical ESP32/Arduino test (firmware compiled only; no USB board detected on this machine).
- Accessibility audit, phone camera over HTTPS on a real phone, real webcam capture, optical check on a real LED.
- No detection accuracy, latency, or cost metrics exist yet.
