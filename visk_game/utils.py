from __future__ import annotations

import random
import string
import textwrap
from collections import deque


Color = tuple[int, int, int]


class Colors:
    _mix_cache: dict[tuple[Color, Color, float], Color] = {}
    _mix_cache_order: deque[tuple[Color, Color, float]] = deque()
    _mix_cache_limit = 512

    @classmethod
    def mix(cls, a: Color, b: Color, amount: float) -> Color:
        key = (a, b, amount)
        cached = cls._mix_cache.get(key)
        if cached is not None:
            return cached
        mixed = (
            int(a[0] + (b[0] - a[0]) * amount),
            int(a[1] + (b[1] - a[1]) * amount),
            int(a[2] + (b[2] - a[2]) * amount),
        )
        cls._mix_cache[key] = mixed
        cls._mix_cache_order.append(key)
        if len(cls._mix_cache_order) > cls._mix_cache_limit:
            oldest = cls._mix_cache_order.popleft()
            cls._mix_cache.pop(oldest, None)
        return mixed


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def mix(a: Color, b: Color, amount: float) -> Color:
    return Colors.mix(a, b, amount)


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
