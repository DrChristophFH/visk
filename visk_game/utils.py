from __future__ import annotations

import random
import string
import textwrap


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * amount) for i in range(3))


def fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def bg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def style_reset() -> str:
    return "\x1b[0m"


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def hash_noise(x: int, y: int, seed: int) -> int:
    value = x * 374761393 + y * 668265263 + seed * 1442695040888963407
    value = (value ^ (value >> 13)) & 0xFFFFFFFF
    return value


def random_word(rng: random.Random, length: int) -> str:
    alphabet = string.ascii_lowercase
    return "".join(rng.choice(alphabet) for _ in range(length))


def wrap_lines(text: str, width: int) -> list[str]:
    if width <= 4:
        return [text[:width]]
    return textwrap.wrap(text, width=width, replace_whitespace=False, drop_whitespace=False) or [""]


def arrow_for_direction(direction: str) -> str:
    return {
        "up": "^",
        "down": "v",
        "left": "<",
        "right": ">",
    }[direction]


def invert_colors(
    fg_color: tuple[int, int, int],
    bg_color: tuple[int, int, int],
    blink_on: bool,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if blink_on:
        return bg_color, fg_color
    return fg_color, bg_color
