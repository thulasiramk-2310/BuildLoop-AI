# Hardware setup

Low-voltage USB-powered demo only. No mains, motors, batteries or high-current loads.

## Parts
ESP32-DevKitC V4 (ESP32-WROOM-32), breadboard, LED (colour noted), ~220 Ω resistor (LED), photoresistor + ~10 kΩ resistor, jumpers. Do not use this pin map on ESP32-S2/S3/C3.

## Wiring (matches the generated design)
| From | To |
|---|---|
| GPIO25 | 220 Ω resistor → LED anode (long leg) |
| LED cathode | GND |
| 3V3 | photoresistor leg A |
| photoresistor leg B | GPIO34 **and** 10 kΩ resistor |
| 10 kΩ other end | GND |

GPIO34 is ADC1, input-only. ESP32 pins are not 5 V tolerant: never connect 5V/VIN to a GPIO. Brighter light → higher ADC (LDR on the high side); dark threshold ≈ 37 % of full scale (≈1515 counts).

Estimated LED current (3.3 V − 2.0 V) / 220 Ω ≈ 5.9 mA — a calculation, not a measurement.

## Firmware (you run these; BuildLoop never flashes)
```powershell
pip install platformio          # not installed on the dev machine yet
cd firmware\esp32
pio run                         # compile check
pio device list                 # find the port
pio run -t upload --upload-port COM5   # replace COM5; hold BOOT if required
```
Status: firmware source written; **not yet compiled or flashed**. Uno variant: `firmware/arduino/buildloop_uno` (ArduinoJson 7), also unverified.

## Protocol v1
Newline-delimited JSON at 115200 baud. Host → device: `{"v":1,"id":"7","cmd":"set_led","args":{"pin":25,"on":true}}`. Device → host: `{"v":1,"id":"7","type":"ack","ok":true,"data":{"led":true}}`, errors `type:"error"` with `data.code`, and periodic `{"v":1,"type":"telemetry","data":{"t":..,"adc":..,"led":..}}`. Whitelist: hello, heartbeat, get_capabilities, read_sensor, set_led, run_test, stop. Safe defaults: LED off at boot, on stop, on 10 s host silence, and when a test ends.

## Selecting the board in the app
Hardware tab → pick the COM port → Connect. Handshake must report `buildloop-esp32`. Other devices are rejected as unsupported. Opening a port may reset the board.
