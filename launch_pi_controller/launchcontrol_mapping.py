from __future__ import annotations

from typing import Final


TRACK_BUTTON_NOTES: Final[list[int]] = [9, 10, 11, 12, 25, 26, 27, 28]
ROW1_KNOB_CCS: Final[list[int]] = [21, 22, 23, 24, 25, 26, 27, 28]
ROW2_KNOB_CCS: Final[list[int]] = [41, 42, 43, 44, 45, 46, 47, 48]
ARROW_CCS: Final[dict[str, int]] = {
    "up": 114,
    "down": 115,
    "left": 116,
    "right": 117,
}


def knob_key(row: int, index: int) -> str:
    return f"knob_{row}_{index}"


def track_key(index: int) -> str:
    return f"track_{index}"


def arrow_key(name: str) -> str:
    return f"arrow_{name}"


def launch_control_led_color(raw_value: int) -> tuple[int, int, int]:
    value = max(0, min(int(raw_value), 127))
    if value == 0:
        return (38, 45, 54)
    if value <= 15:
        scale = value / 15.0
        return (int(120 + 110 * scale), int(60 + 90 * scale), 25)
    if value <= 31:
        scale = (value - 16) / 15.0
        return (int(40 + 30 * scale), int(120 + 90 * scale), int(35 + 50 * scale))
    if value <= 63:
        scale = (value - 32) / 31.0
        return (int(35 + 20 * scale), int(100 + 90 * scale), int(135 + 80 * scale))
    return (220, 228, 236)
