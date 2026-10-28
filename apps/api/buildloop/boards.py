"""Board-specific pin database. Never share a pin map across board variants."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BoardProfile:
    id: str
    name: str
    logic_v: float
    five_v_tolerant: bool
    gpio_max_ma: float  # conservative sustained per-pin source current
    adc_max_counts: int
    output_pins: frozenset[int]
    adc_pins: frozenset[int]  # ADC pins usable alongside WiFi / safe for analog reads
    input_only: frozenset[int] = frozenset()
    reserved: frozenset[int] = frozenset()  # flash/USB - never use
    strapping: frozenset[int] = frozenset()  # usable with caution
    power_pins: dict[str, float] = field(default_factory=dict)  # label -> volts
    demo_led_pin: int = 0
    demo_adc_pin: int = 0
    notes: tuple[str, ...] = ()

    def pin_label(self, pin: int) -> str:
        return f"GPIO{pin}" if self.id.startswith("esp32") else (f"A{pin - 14}" if pin >= 14 else f"D{pin}")


ESP32_DEVKITC_V4 = BoardProfile(
    id="esp32-devkitc-v4",
    name="ESP32-DevKitC V4 (ESP32-WROOM-32)",
    logic_v=3.3,
    five_v_tolerant=False,
    gpio_max_ma=12.0,
    adc_max_counts=4095,
    # GPIO34-39 are input only; GPIO6-11 are connected to SPI flash.
    output_pins=frozenset({0, 2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33}),
    # ADC1 only (GPIO32-39); ADC2 conflicts with WiFi.
    adc_pins=frozenset({32, 33, 34, 35, 36, 39}),
    input_only=frozenset({34, 35, 36, 39}),
    reserved=frozenset({6, 7, 8, 9, 10, 11, 1, 3}),  # 1/3 = USB serial TX/RX used by BuildLoop protocol
    strapping=frozenset({0, 2, 5, 12, 15}),
    power_pins={"3V3": 3.3, "5V": 5.0, "GND": 0.0},
    demo_led_pin=25,
    demo_adc_pin=34,
    notes=("GPIO34-39 input only", "ADC2 unusable with WiFi", "Pins are NOT 5V tolerant"),
)

ARDUINO_UNO = BoardProfile(
    id="arduino-uno",
    name="Arduino Uno R3",
    logic_v=5.0,
    five_v_tolerant=True,
    gpio_max_ma=20.0,
    adc_max_counts=1023,
    output_pins=frozenset(range(2, 14)) | frozenset({14, 15, 16, 17, 18, 19}),
    adc_pins=frozenset({14, 15, 16, 17, 18, 19}),  # A0-A5
    reserved=frozenset({0, 1}),
    power_pins={"3V3": 3.3, "5V": 5.0, "GND": 0.0},
    demo_led_pin=9,
    demo_adc_pin=14,
    notes=("5V logic", "A0-A5 = D14-D19"),
)

BOARDS: dict[str, BoardProfile] = {b.id: b for b in (ESP32_DEVKITC_V4, ARDUINO_UNO)}


def get_board(board_id: str) -> BoardProfile:
    try:
        return BOARDS[board_id]
    except KeyError:
        raise KeyError(f"unsupported board '{board_id}'; supported: {sorted(BOARDS)}") from None
