"""Read-only hardware diagnostics. Enumerates ports WITHOUT opening them (opening can reset a board)."""
from __future__ import annotations

from typing import Any

import serial.tools.list_ports

# USB-serial bridge chips commonly found on ESP32/Arduino boards. A match is a hint, never proof of the board.
BRIDGES: dict[tuple[int, int], tuple[str, str]] = {
    (0x10C4, 0xEA60): ("Silicon Labs CP210x", "common on ESP32-DevKitC V4"),
    (0x1A86, 0x7523): ("WCH CH340", "common on ESP32 clones and Arduino Uno clones"),
    (0x1A86, 0x55D4): ("WCH CH9102", "common on newer ESP32 DevKits"),
    (0x0403, 0x6001): ("FTDI FT232", "common on older Arduinos"),
    (0x2341, 0x0043): ("Arduino Uno R3", "official Arduino Uno"),
    (0x303A, 0x1001): ("Espressif USB-JTAG/serial", "native USB on ESP32-S3/C3 (NOT the supported DevKitC V4 profile)"),
}

WIRING = [
    {"from": "GPIO25", "to": "220 ohm resistor, then LED anode (long leg)"},
    {"from": "LED cathode", "to": "GND"},
    {"from": "3V3", "to": "photoresistor leg A"},
    {"from": "photoresistor leg B", "to": "GPIO34 and 10 kohm resistor"},
    {"from": "10 kohm other end", "to": "GND"},
]

RECOVERY = {
    "no_ports": "No serial ports. Plug in the board with a data-capable USB cable and install the CP210x/CH340 driver.",
    "handshake_failed": "Board did not answer the handshake. Flash BuildLoop firmware (docs/hardware/SETUP.md) and pick the right port.",
    "lost": "Heartbeat lost. Check the cable and reconnect; the LED turns off after 10 s of host silence.",
    "unsupported_device": "Firmware identifies as something other than BuildLoop. Reflash the BuildLoop firmware.",
}


def enumerate_ports() -> list[dict[str, Any]]:
    out = []
    for p in serial.tools.list_ports.comports():
        bridge = BRIDGES.get((p.vid, p.pid)) if p.vid is not None and p.pid is not None else None
        out.append(
            {
                "device": p.device,
                "description": p.description,
                "vid": f"{p.vid:04X}" if p.vid is not None else None,
                "pid": f"{p.pid:04X}" if p.pid is not None else None,
                "manufacturer": p.manufacturer,
                "serial_number": p.serial_number,
                "bridge": bridge[0] if bridge else None,
                "candidate_note": bridge[1] if bridge else "Unknown USB device (may be a Bluetooth/virtual port, not a board).",
                "likely_board": bool(bridge),
            }
        )
    return out


def diagnostics(session_snapshot: dict | None) -> dict[str, Any]:
    ports = enumerate_ports()
    hw = session_snapshot or {"status": "disconnected"}
    errors = []
    if not ports:
        errors.append({"code": "no_ports", "recovery": RECOVERY["no_ports"]})
    if hw.get("status") == "lost":
        errors.append({"code": "lost", "recovery": RECOVERY["lost"]})
    return {
        "ports": ports,
        "opened_any_port": False,
        "expected_protocol": {"version": 1, "framing": "newline-delimited JSON", "baud": 115200, "firmware": "buildloop-esp32", "handshake": "hello", "commands": ["hello", "heartbeat", "get_capabilities", "read_sensor", "set_led", "run_test", "stop"]},
        "required_board_profile": {"id": "esp32-devkitc-v4", "name": "ESP32-DevKitC V4 (ESP32-WROOM-32)", "led_pin": 25, "adc_pin": 34},
        "wiring": WIRING,
        "connection": {"status": hw.get("status", "disconnected"), "last_heartbeat": hw.get("last_heartbeat"), "last_telemetry": (hw.get("latest") or {}).get("at"), "simulated": hw.get("simulated", False)},
        "errors": errors,
        "recovery": RECOVERY,
        "validation_sequence": [
            "Connect to the approved port (explicit approval required)",
            "Firmware handshake (hello)",
            "Verify protocol version 1",
            "Receive telemetry heartbeat",
            "Confirm safe defaults: LED reported OFF before any command",
            "Test current-limited LED after you confirm wiring",
            "Compare commanded vs reported state",
            "Capture independent optical evidence (camera on LED region)",
        ],
    }
