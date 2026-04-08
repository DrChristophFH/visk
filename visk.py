from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import random
import select
import shutil
import string
import sys
import textwrap
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


SAVE_PATH = Path(__file__).with_name("visk_save.json")
CHUNK_SIZE = 28
GENERATION_RADIUS = 2
MAX_ENEMY_LENGTH = 200

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

ABILITY_NAMES = ("zap", "bomb", "mine", "silence", "ping", "dash")
SECTOR_NAMES = ("MAINFRAME", "ARCHIVE", "SUBROUTINE", "BLACKSITE", "NULLNET", "SECTOR-7")
THEMES = (
    {
        "name": "noir",
        "bg": (10, 10, 12),
        "bg_alt": (14, 14, 17),
        "wall": (78, 79, 88),
        "floor": (12, 12, 14),
        "player": (235, 236, 241),
        "player_pending": (208, 212, 222),
        "enemy": (205, 108, 120),
        "enemy_alt": (143, 121, 212),
        "pickup": (171, 227, 146),
        "bytes": (130, 171, 244),
        "accent": (196, 132, 255),
        "muted": (82, 84, 92),
        "ping": (169, 150, 255),
        "blind": (8, 8, 10),
    },
)
NOISE_GLYPHS = "  .`:"
RUN_ART = (
    "██╗   ██╗██╗███████╗██╗  ██╗",
    "██║   ██║██║██╔════╝██║ ██╔╝",
    "██║   ██║██║███████╗█████╔╝ ",
    "╚██╗ ██╔╝██║╚════██║██╔═██╗ ",
    " ╚████╔╝ ██║███████║██║  ██╗",
    "  ╚═══╝  ╚═╝╚══════╝╚═╝  ╚═╝",
)


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


@dataclass
class Cell:
    ch: str = " "
    fg: tuple[int, int, int] | None = None
    bg: tuple[int, int, int] | None = None
    bold: bool = False


class Canvas:
    def __init__(self, width: int, height: int, background: tuple[int, int, int]) -> None:
        self.width = width
        self.height = height
        self.cells = [[Cell(" ", None, background, False) for _ in range(width)] for _ in range(height)]

    def put(
        self,
        x: int,
        y: int,
        ch: str,
        *,
        fg_color: tuple[int, int, int] | None = None,
        bg_color: tuple[int, int, int] | None = None,
        bold: bool = False,
    ) -> None:
        if 0 <= x < self.width and 0 <= y < self.height and ch:
            existing = self.cells[y][x]
            self.cells[y][x] = Cell(ch[0], fg_color, existing.bg if bg_color is None else bg_color, bold)

    def text(
        self,
        x: int,
        y: int,
        text: str,
        *,
        fg_color: tuple[int, int, int] | None = None,
        bg_color: tuple[int, int, int] | None = None,
        bold: bool = False,
    ) -> None:
        for offset, ch in enumerate(text):
            self.put(x + offset, y, ch, fg_color=fg_color, bg_color=bg_color, bold=bold)

    def fill_noise(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        base: tuple[int, int, int],
        alt: tuple[int, int, int],
        seed: int,
    ) -> None:
        for row in range(max(0, y), min(self.height, y + height)):
            for col in range(max(0, x), min(self.width, x + width)):
                value = hash_noise(col, row, seed)
                if value % 17 == 0:
                    glyph = NOISE_GLYPHS[value % len(NOISE_GLYPHS)]
                    self.put(col, row, glyph, fg_color=mix(base, alt, 0.4), bg_color=base)
                elif self.cells[row][col].ch == " ":
                    self.cells[row][col].bg = mix(base, alt, ((row + col) % 7) / 12)

    def render(self) -> str:
        parts: list[str] = ["\x1b[H"]
        current = ("", "", False)
        for row_index, row in enumerate(self.cells):
            for cell in row:
                fg_code = fg(cell.fg) if cell.fg else ""
                bg_code = bg(cell.bg) if cell.bg else ""
                style = (fg_code, bg_code, cell.bold)
                if style != current:
                    parts.append(style_reset())
                    if cell.bold:
                        parts.append("\x1b[1m")
                    parts.append(fg_code)
                    parts.append(bg_code)
                    current = style
                parts.append(cell.ch)
            if row_index < self.height - 1:
                parts.append(style_reset())
                parts.append("\n")
            current = ("", "", False)
        parts.append(style_reset())
        return "".join(parts)

    def render_full(self) -> str:
        return "\x1b[2J" + self.render()

    def render_diff(self, previous: "Canvas | None") -> str:
        if previous is None or previous.width != self.width or previous.height != self.height:
            return self.render_full()

        parts: list[str] = []
        current_style: tuple[tuple[int, int, int] | None, tuple[int, int, int] | None, bool] | None = None
        for y in range(self.height):
            x = 0
            while x < self.width:
                if self.cells[y][x] == previous.cells[y][x]:
                    x += 1
                    continue
                parts.append(f"\x1b[{y + 1};{x + 1}H")
                current_style = None
                while x < self.width and self.cells[y][x] != previous.cells[y][x]:
                    cell = self.cells[y][x]
                    style = (cell.fg, cell.bg, cell.bold)
                    if style != current_style:
                        parts.append(style_reset())
                        if cell.bold:
                            parts.append("\x1b[1m")
                        if cell.fg:
                            parts.append(fg(cell.fg))
                        if cell.bg:
                            parts.append(bg(cell.bg))
                        current_style = style
                    parts.append(cell.ch)
                    x += 1
        if not parts:
            return ""
        parts.append(style_reset())
        return "".join(parts)


@dataclass
class Segment:
    x: int
    y: int
    ch: str
    infected: int = 0


@dataclass
class Pickup:
    x: int
    y: int
    text: str
    ability: str
    failed: bool = False
    resolved: bool = False

    def cells(self) -> list[tuple[int, int]]:
        return [(self.x + i, self.y) for i in range(len(self.text))]


@dataclass
class ByteShard:
    x: int
    y: int
    value: int


@dataclass
class Bomb:
    x: int
    y: int
    fuse: int
    radius: int
    owner: str = "player"


@dataclass
class Mine:
    x: int
    y: int
    radius: int = 1


@dataclass
class Enemy:
    kind: str
    body: deque[Segment]
    heading: tuple[int, int]
    speed_bias: float = 0.75
    fuse_timer: int = 0
    stunned: int = 0
    dead: bool = False

    @property
    def head(self) -> Segment:
        return self.body[-1]


@dataclass
class PickupAttempt:
    pickup_index: int
    reverse: bool
    progress: int


@dataclass
class Sector:
    width: int
    height: int
    walls: set[tuple[int, int]]
    pickups: list[Pickup]
    byte_shards: list[ByteShard]
    enemies: list[Enemy]
    exit: tuple[int, int]
    name: str
    seed: int
    theme: dict[str, tuple[int, int, int] | str]
    start: tuple[int, int]
    generated_chunks: set[tuple[int, int]]
    chunk_size: int = CHUNK_SIZE


@dataclass
class SaveData:
    banked_bytes: int = 0
    streak: int = 0
    hardcore: bool = False
    upgrades: dict[str, int] = field(
        default_factory=lambda: {
            "dash_cache": 0,
            "ping_cache": 0,
            "magnet": 0,
            "focus": 0,
        }
    )


@dataclass
class RunState:
    sector: Sector
    body: deque[Segment]
    direction: str
    pending_command: str = ""
    direction_undos: list[tuple[int, str, str]] = field(default_factory=list)
    bytes_collected: int = 0
    inventory: dict[str, int] = field(default_factory=dict)
    ping_path: list[tuple[int, int]] = field(default_factory=list)
    ping_ticks: int = 0
    blind_ticks: int = 0
    silence_ticks: int = 0
    bombs: list[Bomb] = field(default_factory=list)
    mines: list[Mine] = field(default_factory=list)
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=6))
    pickup_attempt: PickupAttempt | None = None
    game_over: bool = False
    extracted: bool = False
    cause: str = ""
    ticks: int = 0
    kills: int = 0
    hardcore: bool = False
    camera_left: int | None = None
    camera_top: int | None = None

    @property
    def head(self) -> Segment:
        return self.body[-1]

    def log(self, message: str) -> None:
        self.messages.appendleft(message)


SHOP_ITEMS = (
    {
        "id": "dash_cache",
        "name": "DASH CACHE",
        "description": "Start each run with +1 DASH charge.",
        "base_cost": 140,
    },
    {
        "id": "ping_cache",
        "name": "PING CACHE",
        "description": "Start each run with +1 PING charge.",
        "base_cost": 120,
    },
    {
        "id": "magnet",
        "name": "BYTE MAGNET",
        "description": "Increase shard value by 20% per level.",
        "base_cost": 160,
    },
    {
        "id": "focus",
        "name": "FOCUS MASK",
        "description": "Reduce BLINDER duration by 3 turns per level.",
        "base_cost": 130,
    },
)


class TerminalController:
    def __init__(self) -> None:
        self.is_windows = os.name == "nt"
        self.original_termios = None
        self.stdin_fd = None
        self.console_mode = None
        self.in_alt_screen = False
        self.cursor_hidden = False

    def __enter__(self) -> "TerminalController":
        sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[?25l")
        sys.stdout.flush()
        self.in_alt_screen = True
        self.cursor_hidden = True
        if self.is_windows:
            self._enter_windows()
        else:
            self._enter_posix()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.is_windows:
            self._exit_windows()
        else:
            self._exit_posix()
        if self.cursor_hidden or self.in_alt_screen:
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        self.cursor_hidden = False
        self.in_alt_screen = False

    def _enter_windows(self) -> None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            self.console_mode = mode.value
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)

    def _exit_windows(self) -> None:
        if self.console_mode is not None:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            kernel32.SetConsoleMode(handle, self.console_mode)

    def _enter_posix(self) -> None:
        import termios
        import tty

        self.stdin_fd = sys.stdin.fileno()
        self.original_termios = termios.tcgetattr(self.stdin_fd)
        tty.setcbreak(self.stdin_fd)

    def _exit_posix(self) -> None:
        if self.original_termios is None or self.stdin_fd is None:
            return
        import termios

        termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.original_termios)

    def read_key(self, timeout: float | None) -> str | None:
        if self.is_windows:
            import msvcrt

            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        special = msvcrt.getwch()
                        mapping = {"K": "LEFT", "M": "RIGHT", "H": "UP", "P": "DOWN"}
                        return mapping.get(special, None)
                    if ch == "\r":
                        return "ENTER"
                    if ch == "\x08":
                        return "BACKSPACE"
                    if ch == "\t":
                        return "TAB"
                    if ch == "\x1b":
                        return "ESC"
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    return ch
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)
        else:
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                ready, _, _ = select.select([sys.stdin], [], [], remaining)
                if not ready:
                    return None
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    ready, _, _ = select.select([sys.stdin], [], [], 0.0001)
                    if ready:
                        next1 = sys.stdin.read(1)
                        next2 = sys.stdin.read(1) if select.select([sys.stdin], [], [], 0.0001)[0] else ""
                        mapping = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}
                        return mapping.get(next1 + next2, "ESC")
                    return "ESC"
                if ch in ("\r", "\n"):
                    return "ENTER"
                if ch in ("\x7f", "\b"):
                    return "BACKSPACE"
                if ch == "\t":
                    return "TAB"
                if ch == "\x03":
                    raise KeyboardInterrupt
                return ch


def load_save() -> SaveData:
    if not SAVE_PATH.exists():
        return SaveData()
    try:
        raw = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SaveData()
    data = SaveData()
    data.banked_bytes = int(raw.get("banked_bytes", 0))
    data.streak = int(raw.get("streak", 0))
    data.hardcore = bool(raw.get("hardcore", False))
    upgrades = raw.get("upgrades", {})
    for key in data.upgrades:
        data.upgrades[key] = int(upgrades.get(key, 0))
    return data


def save_save(data: SaveData) -> None:
    payload = {
        "banked_bytes": data.banked_bytes,
        "streak": data.streak,
        "hardcore": data.hardcore,
        "upgrades": data.upgrades,
    }
    SAVE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def random_word(rng: random.Random, length: int) -> str:
    alphabet = string.ascii_lowercase
    return "".join(rng.choice(alphabet) for _ in range(length))


def bfs(
    start: tuple[int, int],
    goal: tuple[int, int],
    width: int,
    height: int,
    walls: set[tuple[int, int]],
    blocked: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    blocked = blocked or set()
    queue: deque[tuple[int, int]] = deque([start])
    came_from = {start: None}
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for dx, dy in DIRECTIONS.values():
            nxt = current[0] + dx, current[1] + dy
            if not (1 <= nxt[0] < width - 1 and 1 <= nxt[1] < height - 1):
                continue
            if nxt in walls or nxt in blocked or nxt in came_from:
                continue
            came_from[nxt] = current
            queue.append(nxt)
    if goal not in came_from:
        return []
    path = []
    cursor = goal
    while cursor is not None:
        path.append(cursor)
        cursor = came_from[cursor]
    path.reverse()
    return path


def pick_empty_floor(
    rng: random.Random,
    width: int,
    height: int,
    walls: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    margin: int = 2,
) -> tuple[int, int]:
    for _ in range(1000):
        x = rng.randint(margin, width - margin - 1)
        y = rng.randint(margin, height - margin - 1)
        if (x, y) not in walls and (x, y) not in occupied:
            return x, y
    raise RuntimeError("failed to place entity")


def reserve_zone(center: tuple[int, int], radius: int) -> set[tuple[int, int]]:
    cx, cy = center
    cells = set()
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if manhattan((x, y), center) <= radius + 1:
                cells.add((x, y))
    return cells


def box_shape(x: int, y: int, width: int, height: int) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for xx in range(x, x + width):
        cells.add((xx, y))
        cells.add((xx, y + height - 1))
    for yy in range(y, y + height):
        cells.add((x, yy))
        cells.add((x + width - 1, yy))
    return cells


def diamond_shape(cx: int, cy: int, radius: int) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for dy in range(-radius, radius + 1):
        span = radius - abs(dy)
        cells.add((cx - span, cy + dy))
        cells.add((cx + span, cy + dy))
    return cells


def l_shape(x: int, y: int, width: int, height: int, horizontal_first: bool) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    if horizontal_first:
        for xx in range(x, x + width):
            cells.add((xx, y))
        for yy in range(y, y + height):
            cells.add((x, yy))
    else:
        for yy in range(y, y + height):
            cells.add((x, yy))
        for xx in range(x, x + width):
            cells.add((xx, y + height - 1))
    return cells


def bar_shape(x: int, y: int, length: int, horizontal: bool) -> set[tuple[int, int]]:
    if horizontal:
        return {(x + offset, y) for offset in range(length)}
    return {(x, y + offset) for offset in range(length)}


def chunk_coords(x: int, y: int, chunk_size: int = CHUNK_SIZE) -> tuple[int, int]:
    return x // chunk_size, y // chunk_size


def chunk_origin(cx: int, cy: int, chunk_size: int = CHUNK_SIZE) -> tuple[int, int]:
    return cx * chunk_size, cy * chunk_size


def reserve_radius(center: tuple[int, int], point: tuple[int, int], radius: int) -> bool:
    return manhattan(center, point) <= radius


def is_reserved_world(sector: Sector, point: tuple[int, int]) -> bool:
    return reserve_radius(sector.start, point, 5) or reserve_radius(sector.exit, point, 6)


def sector_occupied_cells(sector: Sector) -> set[tuple[int, int]]:
    occupied = set(sector.walls)
    occupied.add(sector.exit)
    for pickup in sector.pickups:
        if pickup.resolved:
            continue
        occupied.update(pickup.cells())
    for shard in sector.byte_shards:
        occupied.add((shard.x, shard.y))
    for enemy in sector.enemies:
        if enemy.dead:
            continue
        occupied.update((segment.x, segment.y) for segment in enemy.body)
    return occupied


def sample_chunk_floor(
    rng: random.Random,
    sector: Sector,
    cx: int,
    cy: int,
    occupied: set[tuple[int, int]],
    *,
    width: int = 1,
) -> tuple[int, int] | None:
    base_x, base_y = chunk_origin(cx, cy, sector.chunk_size)
    for _ in range(160):
        x = rng.randint(base_x + 2, base_x + sector.chunk_size - width - 3)
        y = rng.randint(base_y + 2, base_y + sector.chunk_size - 3)
        cells = {(x + offset, y) for offset in range(width)}
        if any(cell in occupied or is_reserved_world(sector, cell) for cell in cells):
            continue
        return x, y
    return None


def generate_chunk(sector: Sector, cx: int, cy: int) -> None:
    if (cx, cy) in sector.generated_chunks:
        return
    sector.generated_chunks.add((cx, cy))
    rng = random.Random(hash_noise(cx, cy, sector.seed))
    occupied = sector_occupied_cells(sector)
    base_x, base_y = chunk_origin(cx, cy, sector.chunk_size)
    near_start = manhattan((base_x + sector.chunk_size // 2, base_y + sector.chunk_size // 2), sector.start) <= sector.chunk_size * 2
    near_exit = manhattan((base_x + sector.chunk_size // 2, base_y + sector.chunk_size // 2), sector.exit) <= sector.chunk_size * 2

    shape_count = rng.randint(0, 2) if near_start else rng.randint(1, 4)
    for _ in range(shape_count):
        shape_type = rng.choice(("box", "diamond", "l", "bar"))
        if shape_type == "box":
            w = rng.randint(4, 9)
            h = rng.randint(3, 6)
            x = rng.randint(base_x + 2, base_x + sector.chunk_size - w - 3)
            y = rng.randint(base_y + 2, base_y + sector.chunk_size - h - 3)
            cells = box_shape(x, y, w, h)
        elif shape_type == "diamond":
            radius = rng.randint(2, 4)
            x = rng.randint(base_x + radius + 2, base_x + sector.chunk_size - radius - 3)
            y = rng.randint(base_y + radius + 2, base_y + sector.chunk_size - radius - 3)
            cells = diamond_shape(x, y, radius)
        elif shape_type == "l":
            w = rng.randint(4, 8)
            h = rng.randint(4, 7)
            x = rng.randint(base_x + 2, base_x + sector.chunk_size - w - 3)
            y = rng.randint(base_y + 2, base_y + sector.chunk_size - h - 3)
            cells = l_shape(x, y, w, h, horizontal_first=rng.random() < 0.5)
        else:
            horizontal = rng.random() < 0.5
            length = rng.randint(5, 11)
            x = rng.randint(base_x + 2, base_x + sector.chunk_size - (length if horizontal else 1) - 3)
            y = rng.randint(base_y + 2, base_y + sector.chunk_size - (1 if horizontal else length) - 3)
            cells = bar_shape(x, y, length, horizontal)
        padded = set(cells)
        for cell_x, cell_y in list(cells):
            for ny in range(cell_y - 1, cell_y + 2):
                for nx in range(cell_x - 1, cell_x + 2):
                    padded.add((nx, ny))
        if any(cell in occupied or is_reserved_world(sector, cell) for cell in padded):
            continue
        sector.walls.update(cells)
        occupied.update(cells)

    pickup_total = 0 if near_start else rng.randint(0, 1)
    for _ in range(pickup_total):
        ability = rng.choice(ABILITY_NAMES)
        label = f"pickup_{ability}"
        point = sample_chunk_floor(rng, sector, cx, cy, occupied, width=len(label))
        if point is None:
            continue
        pickup = Pickup(x=point[0], y=point[1], text=label, ability=ability)
        sector.pickups.append(pickup)
        occupied.update(pickup.cells())

    shard_total = rng.randint(1, 3 if not near_exit else 2)
    for _ in range(shard_total):
        point = sample_chunk_floor(rng, sector, cx, cy, occupied)
        if point is None:
            continue
        sector.byte_shards.append(ByteShard(point[0], point[1], rng.randint(24, 68)))
        occupied.add(point)

    enemy_total = 0 if near_start else rng.randint(0, 1)
    for _ in range(enemy_total):
        length = rng.randint(4, 7)
        point = sample_chunk_floor(rng, sector, cx, cy, occupied, width=length)
        if point is None:
            continue
        x, y = point
        cells = {(x + offset, y) for offset in range(length)}
        if any(cell in occupied for cell in cells):
            continue
        body = deque(Segment(x + offset, y, random_word(rng, 1)) for offset in range(length))
        kind = rng.choice(("chaser", "virus", "blinder", "fuse"))
        sector.enemies.append(Enemy(kind=kind, body=body, heading=(1, 0), speed_bias=0.85 if kind == "chaser" else 0.72))
        occupied.update(cells)


def ensure_generated_around(sector: Sector, center: tuple[int, int], radius: int = GENERATION_RADIUS) -> None:
    chunk_x, chunk_y = chunk_coords(center[0], center[1], sector.chunk_size)
    for cy in range(chunk_y - radius, chunk_y + radius + 1):
        for cx in range(chunk_x - radius, chunk_x + radius + 1):
            generate_chunk(sector, cx, cy)


def ensure_generated_rect(sector: Sector, left: int, top: int, width: int, height: int) -> None:
    start_cx, start_cy = chunk_coords(left, top, sector.chunk_size)
    end_cx, end_cy = chunk_coords(left + width, top + height, sector.chunk_size)
    for cy in range(start_cy - 1, end_cy + 2):
        for cx in range(start_cx - 1, end_cx + 2):
            generate_chunk(sector, cx, cy)


def bfs_world(
    start: tuple[int, int],
    goal: tuple[int, int],
    walls: set[tuple[int, int]],
    blocked: set[tuple[int, int]] | None = None,
    *,
    bounds: tuple[int, int, int, int],
) -> list[tuple[int, int]]:
    blocked = blocked or set()
    min_x, min_y, max_x, max_y = bounds
    queue: deque[tuple[int, int]] = deque([start])
    came_from = {start: None}
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for dx, dy in DIRECTIONS.values():
            nxt = current[0] + dx, current[1] + dy
            if nxt[0] < min_x or nxt[0] > max_x or nxt[1] < min_y or nxt[1] > max_y:
                continue
            if nxt in walls or nxt in blocked or nxt in came_from:
                continue
            came_from[nxt] = current
            queue.append(nxt)
    if goal not in came_from:
        return []
    path = []
    cursor = goal
    while cursor is not None:
        path.append(cursor)
        cursor = came_from[cursor]
    path.reverse()
    return path


def place_sparse_obstacles(
    rng: random.Random,
    width: int,
    height: int,
    walls: set[tuple[int, int]],
    reserved: set[tuple[int, int]],
) -> None:
    target_shapes = rng.randint(10, 15)
    attempts = 0
    placed = 0
    while placed < target_shapes and attempts < target_shapes * 30:
        attempts += 1
        shape_type = rng.choice(("box", "diamond", "l", "bar"))
        if shape_type == "box":
            w = rng.randint(5, 10)
            h = rng.randint(4, 7)
            x = rng.randint(3, width - w - 4)
            y = rng.randint(3, height - h - 4)
            cells = box_shape(x, y, w, h)
        elif shape_type == "diamond":
            radius = rng.randint(2, 4)
            cx = rng.randint(radius + 3, width - radius - 4)
            cy = rng.randint(radius + 3, height - radius - 4)
            cells = diamond_shape(cx, cy, radius)
        elif shape_type == "l":
            w = rng.randint(4, 9)
            h = rng.randint(4, 8)
            x = rng.randint(3, width - w - 4)
            y = rng.randint(3, height - h - 4)
            cells = l_shape(x, y, w, h, horizontal_first=rng.random() < 0.5)
        else:
            horizontal = rng.random() < 0.5
            length = rng.randint(5, 12)
            x = rng.randint(3, width - (length if horizontal else 1) - 4)
            y = rng.randint(3, height - (1 if horizontal else length) - 4)
            cells = bar_shape(x, y, length, horizontal)

        padded = set(cells)
        for cell_x, cell_y in list(cells):
            for ny in range(cell_y - 1, cell_y + 2):
                for nx in range(cell_x - 1, cell_x + 2):
                    padded.add((nx, ny))

        if any(
            cell in reserved
            or cell in walls
            or cell[0] <= 1
            or cell[1] <= 1
            or cell[0] >= width - 2
            or cell[1] >= height - 2
            for cell in padded
        ):
            continue
        walls.update(cells)
        placed += 1


def generate_sector(save: SaveData, *, seed: int | None = None) -> tuple[Sector, tuple[int, int]]:
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    start = (0, 0)
    distance = rng.randint(90, 140)
    direction = rng.choice(tuple(DIRECTIONS.values()))
    lateral = rng.randint(-18, 18)
    if direction[0]:
        exit_pos = (direction[0] * distance, lateral)
    else:
        exit_pos = (lateral, direction[1] * distance)

    sector = Sector(
        width=0,
        height=0,
        walls=set(),
        pickups=[],
        byte_shards=[],
        enemies=[],
        exit=exit_pos,
        name=rng.choice(SECTOR_NAMES),
        seed=rng.randint(0, 1 << 30),
        theme=rng.choice(THEMES),
        start=start,
        generated_chunks=set(),
    )
    ensure_generated_around(sector, start, GENERATION_RADIUS)
    ensure_generated_around(sector, exit_pos, 1)
    return sector, start


def create_run(save: SaveData) -> RunState:
    sector, start = generate_sector(save)
    body = deque([Segment(start[0], start[1], " ")])
    inventory = {name: 0 for name in ABILITY_NAMES}
    inventory["dash"] += save.upgrades["dash_cache"]
    inventory["ping"] += save.upgrades["ping_cache"]
    run = RunState(sector=sector, body=body, direction="right", inventory=inventory, hardcore=save.hardcore)
    run.log(f"Link established. Sector {sector.name}.")
    run.log("Type a command, then press ENTER. Directions move; ability names execute.")
    return run


def body_positions(body: deque[Segment]) -> set[tuple[int, int]]:
    return {(segment.x, segment.y) for segment in body}


def step_from_direction(direction: str) -> tuple[int, int]:
    return DIRECTIONS[direction]


def enemy_positions(enemies: list[Enemy]) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for enemy in enemies:
        if enemy.dead:
            continue
        for segment in enemy.body:
            cells.add((segment.x, segment.y))
    return cells


def closest_enemy(run: RunState) -> Enemy | None:
    living = [enemy for enemy in run.sector.enemies if not enemy.dead]
    if not living:
        return None
    return min(living, key=lambda enemy: manhattan((enemy.head.x, enemy.head.y), (run.head.x, run.head.y)))


def kill_enemy(run: RunState, enemy: Enemy, reason: str) -> None:
    if enemy.dead:
        return
    enemy.dead = True
    run.kills += 1
    run.bytes_collected += 40
    run.log(f"{enemy.kind.upper()} deleted via {reason}. +40 bytes.")


def apply_explosion(run: RunState, x: int, y: int, radius: int, owner: str) -> None:
    player_cells = body_positions(run.body)
    if any(abs(px - x) + abs(py - y) <= radius for px, py in player_cells):
        run.game_over = True
        run.cause = "exploded"
    for enemy in run.sector.enemies:
        if enemy.dead:
            continue
        if any(abs(segment.x - x) + abs(segment.y - y) <= radius for segment in enemy.body):
            kill_enemy(run, enemy, owner.upper())


def update_hazards(run: RunState) -> None:
    remaining_bombs: list[Bomb] = []
    for bomb in run.bombs:
        bomb.fuse -= 1
        if bomb.fuse <= 0:
            apply_explosion(run, bomb.x, bomb.y, bomb.radius, bomb.owner)
            run.log(f"{bomb.owner.upper()} bomb detonated.")
        else:
            remaining_bombs.append(bomb)
    run.bombs = remaining_bombs

    remaining_mines: list[Mine] = []
    for mine in run.mines:
        triggered = False
        for enemy in run.sector.enemies:
            if enemy.dead:
                continue
            if manhattan((mine.x, mine.y), (enemy.head.x, enemy.head.y)) <= mine.radius:
                apply_explosion(run, mine.x, mine.y, 1, "mine")
                run.log("Mine triggered.")
                triggered = True
                break
        if not triggered:
            remaining_mines.append(mine)
    run.mines = remaining_mines


def line_of_floor(sector: Sector, x: int, y: int, steps: int, direction: str, blockers: set[tuple[int, int]]) -> tuple[int, int]:
    dx, dy = step_from_direction(direction)
    cursor = (x, y)
    for _ in range(steps):
        nxt = cursor[0] + dx, cursor[1] + dy
        ensure_generated_around(sector, nxt, 1)
        if nxt in sector.walls or nxt in blockers:
            break
        cursor = nxt
    return cursor


def use_ability(run: RunState, name: str) -> None:
    if run.inventory.get(name, 0) <= 0:
        run.log(f"{name.upper()} unavailable.")
        return
    run.inventory[name] -= 1
    if name == "zap":
        enemy = closest_enemy(run)
        if enemy is None:
            run.log("ZAP found no target.")
            run.inventory[name] += 1
            return
        kill_enemy(run, enemy, "zap")
    elif name == "bomb":
        run.bombs.append(Bomb(run.head.x, run.head.y, fuse=4, radius=2))
        run.log("BOMB armed. Fuse: 4.")
    elif name == "mine":
        run.mines.append(Mine(run.head.x, run.head.y))
        run.log("MINE deployed.")
    elif name == "silence":
        run.silence_ticks = 20
        run.log("SILENCE injected. Enemies paused for 20 ticks.")
    elif name == "ping":
        min_x = min(run.head.x, run.sector.exit[0]) - 18
        max_x = max(run.head.x, run.sector.exit[0]) + 18
        min_y = min(run.head.y, run.sector.exit[1]) - 18
        max_y = max(run.head.y, run.sector.exit[1]) + 18
        ensure_generated_rect(run.sector, min_x, min_y, max_x - min_x, max_y - min_y)
        blocked = enemy_positions(run.sector.enemies) | body_positions(run.body)
        blocked.discard((run.head.x, run.head.y))
        run.ping_path = bfs_world(
            (run.head.x, run.head.y),
            run.sector.exit,
            run.sector.walls,
            blocked,
            bounds=(min_x, min_y, max_x, max_y),
        )
        run.ping_ticks = 18
        run.log("PING resolved route to extraction.")
    elif name == "dash":
        blockers = body_positions(run.body) | enemy_positions(run.sector.enemies)
        blockers.discard((run.head.x, run.head.y))
        target = line_of_floor(run.sector, run.head.x, run.head.y, 4, run.direction, blockers)
        if target == (run.head.x, run.head.y):
            run.log("DASH obstructed.")
            run.inventory[name] += 1
            return
        run.head.x = target[0]
        run.head.y = target[1]
        maybe_collect_byte(run, (target[0], target[1]))
        if target == run.sector.exit:
            run.extracted = True
            run.game_over = True
            run.cause = "extracted"
        run.log("DASH executed.")


def maybe_collect_byte(run: RunState, position: tuple[int, int] | None = None) -> None:
    position = position or (run.head.x, run.head.y)
    for shard in list(run.sector.byte_shards):
        if (shard.x, shard.y) == position:
            run.bytes_collected += shard.value
            run.sector.byte_shards.remove(shard)
            run.log(f"Byte shard extracted. +{shard.value}.")
            return


def begin_or_update_pickup(run: RunState, typed_char: str, position: tuple[int, int] | None = None) -> bool:
    head = position or (run.head.x, run.head.y)
    current_index = None
    current_pickup_index = None
    for idx, pickup in enumerate(run.sector.pickups):
        if pickup.failed or pickup.resolved:
            continue
        for letter_index, cell in enumerate(pickup.cells()):
            if cell == head:
                current_index = letter_index
                current_pickup_index = idx
                break
        if current_pickup_index is not None:
            break

    if run.pickup_attempt is not None:
        pickup = run.sector.pickups[run.pickup_attempt.pickup_index]
        expected_index = (
            len(pickup.text) - 1 - run.pickup_attempt.progress
            if run.pickup_attempt.reverse
            else run.pickup_attempt.progress
        )
        if current_pickup_index != run.pickup_attempt.pickup_index or current_index != expected_index:
            pickup.failed = True
            run.log(f"{pickup.text.upper()} lost in transit.")
            run.pickup_attempt = None
            return True

    if current_pickup_index is None:
        return False

    pickup = run.sector.pickups[current_pickup_index]
    reverse = run.direction == "left"
    if run.pickup_attempt is None:
        valid_start = (not reverse and current_index == 0) or (reverse and current_index == len(pickup.text) - 1)
        if not valid_start:
            pickup.failed = True
            run.log(f"{pickup.text.upper()} corrupted.")
            return True
        expected = pickup.text[current_index]
        if typed_char.lower() != expected:
            pickup.failed = True
            run.log(f"{pickup.text.upper()} mistyped and purged.")
            return True
        run.pickup_attempt = PickupAttempt(current_pickup_index, reverse, 1)
        if len(pickup.text) == 1:
            pickup.resolved = True
            run.inventory[pickup.ability] += 1
            run.log(f"{pickup.ability.upper()} acquired.")
            run.pickup_attempt = None
        return True

    expected = pickup.text[current_index]
    if typed_char.lower() != expected:
        pickup.failed = True
        run.pickup_attempt = None
        run.log(f"{pickup.text.upper()} mistyped and purged.")
        return True
    run.pickup_attempt.progress += 1
    if run.pickup_attempt.progress >= len(pickup.text):
        pickup.resolved = True
        run.inventory[pickup.ability] += 1
        run.log(f"{pickup.ability.upper()} acquired.")
        run.pickup_attempt = None
    return True


def resolve_inline_command(run: RunState) -> None:
    if not run.pending_command:
        return
    suffix = run.pending_command.lower()
    commands = sorted((*ABILITY_NAMES, *DIRECTIONS.keys()), key=len, reverse=True)
    for command in commands:
        if not suffix.endswith(command):
            continue
        if command in DIRECTIONS:
            run.direction_undos.append((len(run.body), run.direction, run.pending_command[:-1]))
            run.direction = command
        else:
            use_ability(run, command)
        run.pending_command = ""
        return


def advance_player(run: RunState, typed_char: str) -> None:
    advance_player_with_mode(run, typed_char, record_command=True)


def advance_player_with_mode(run: RunState, typed_char: str, *, record_command: bool) -> None:
    current_head = run.head
    dx, dy = step_from_direction(run.direction)
    nx = current_head.x + dx
    ny = current_head.y + dy
    next_pos = (nx, ny)
    ensure_generated_around(run.sector, next_pos, 1)
    if next_pos in run.sector.walls:
        run.game_over = True
        run.cause = "wall collision"
        return
    if next_pos in {(segment.x, segment.y) for segment in list(run.body)[:-1]}:
        run.game_over = True
        run.cause = "self collision"
        return
    if next_pos in enemy_positions(run.sector.enemies):
        run.game_over = True
        run.cause = "enemy collision"
        return
    current_position = (current_head.x, current_head.y)
    current_head.ch = typed_char
    maybe_collect_byte(run, current_position)
    if record_command:
        run.pending_command += typed_char
        pickup_touched = begin_or_update_pickup(run, typed_char, current_position)
        if not pickup_touched:
            resolve_inline_command(run)
    run.body.append(Segment(nx, ny, " "))
    if next_pos == run.sector.exit:
        run.extracted = True
        run.game_over = True
        run.cause = "extracted"


def retract_player(run: RunState) -> None:
    if len(run.body) <= 1:
        return
    current_step = len(run.body) - 1
    if run.pending_command:
        run.pending_command = run.pending_command[:-1]
    elif run.direction_undos and run.direction_undos[-1][0] == current_step:
        _, previous_direction, previous_pending = run.direction_undos.pop()
        run.direction = previous_direction
        run.pending_command = previous_pending
    else:
        return
    run.body.pop()
    run.body[-1].ch = " "
    if run.pickup_attempt is not None:
        pickup = run.sector.pickups[run.pickup_attempt.pickup_index]
        pickup.failed = True
        run.log(f"{pickup.text.upper()} lost on rollback.")
        run.pickup_attempt = None


def submit_command(run: RunState) -> None:
    command = run.pending_command.strip().lower()
    if run.pickup_attempt is not None:
        pickup = run.sector.pickups[run.pickup_attempt.pickup_index]
        pickup.failed = True
        run.pickup_attempt = None
        run.log(f"{pickup.text.upper()} lost on submit.")
    if not command:
        run.log("Blank command submitted.")
        return
    if command in DIRECTIONS:
        run.direction = command
        run.log(f"Direction changed to {command.upper()}.")
    elif command in ABILITY_NAMES:
        use_ability(run, command)
    elif command in ("help", "status"):
        run.log("Commands: directions move, ability names execute. Press ENTER to submit.")
    else:
        run.log(f"Unknown command: {command.upper()}.")
    run.pending_command = ""


def resolve_enemy_step(
    run: RunState,
    enemy: Enemy,
    rng: random.Random,
    occupied: set[tuple[int, int]],
) -> tuple[int, int]:
    target = (run.head.x, run.head.y)
    if enemy.kind == "virus":
        target_cells = [segment for segment in run.body if segment.infected <= 0]
        if target_cells:
            target_segment = min(target_cells, key=lambda segment: manhattan((segment.x, segment.y), (enemy.head.x, enemy.head.y)))
            target = (target_segment.x, target_segment.y)
    candidates: list[tuple[int, int]] = []
    for direction in DIRECTIONS.values():
        nxt = enemy.head.x + direction[0], enemy.head.y + direction[1]
        if chunk_coords(nxt[0], nxt[1], run.sector.chunk_size) not in run.sector.generated_chunks:
            continue
        if nxt in run.sector.walls or nxt in occupied:
            continue
        candidates.append(nxt)
    if not candidates:
        return enemy.head.x, enemy.head.y
    if rng.random() < enemy.speed_bias:
        best_distance = min(manhattan(candidate, target) for candidate in candidates)
        best = [candidate for candidate in candidates if manhattan(candidate, target) == best_distance]
        return rng.choice(best)
    return rng.choice(candidates)


def resolve_enemy_effects(run: RunState, enemy: Enemy, save: SaveData) -> None:
    if enemy.dead:
        return
    head = (enemy.head.x, enemy.head.y)
    player_cells = body_positions(run.body)
    if head in player_cells:
        run.game_over = True
        run.cause = f"{enemy.kind} reached your trail"
        return

    if enemy.kind == "virus":
        nearest = min(run.body, key=lambda segment: manhattan((segment.x, segment.y), head))
        if manhattan((nearest.x, nearest.y), head) <= 1:
            nearest.infected = max(nearest.infected, 6)
            stolen = min(14, run.bytes_collected)
            if stolen:
                run.bytes_collected -= stolen
                run.log(f"VIRUS siphoned {stolen} bytes.")
    elif enemy.kind == "blinder":
        if manhattan(head, (run.head.x, run.head.y)) <= 3:
            duration = max(4, 12 - save.upgrades["focus"] * 3)
            run.blind_ticks = max(run.blind_ticks, duration)
            run.log("BLINDER corrupted the feed.")
    elif enemy.kind == "fuse":
        if enemy.fuse_timer > 0:
            enemy.fuse_timer -= 1
            if enemy.fuse_timer == 0:
                apply_explosion(run, enemy.head.x, enemy.head.y, 2, "fuse")
                kill_enemy(run, enemy, "chain")
                run.log("FUSE detonated.")
        elif any(manhattan((segment.x, segment.y), head) <= 2 for segment in run.body):
            enemy.fuse_timer = 2
            run.log("FUSE armed.")


def advance_enemies(run: RunState, save: SaveData, rng: random.Random, *, grow: bool = False) -> None:
    if run.silence_ticks > 0:
        run.silence_ticks -= 1
        return

    ensure_generated_around(run.sector, (run.head.x, run.head.y), GENERATION_RADIUS)
    active_enemies = [enemy for enemy in run.sector.enemies if not enemy.dead]
    occupied = run.sector.walls | body_positions(run.body)
    for enemy in active_enemies:
        for segment in enemy.body:
            occupied.add((segment.x, segment.y))

    for enemy in active_enemies:
        for segment in enemy.body:
            occupied.discard((segment.x, segment.y))
        previous_head = (enemy.head.x, enemy.head.y)
        next_pos = resolve_enemy_step(run, enemy, rng, occupied)
        step_dx = next_pos[0] - enemy.head.x
        step_dy = next_pos[1] - enemy.head.y
        enemy.heading = (step_dx, step_dy)
        enemy.body.append(Segment(next_pos[0], next_pos[1], random.choice("!$%&*+?{}[]/\\<>=")))
        should_grow = grow and len(enemy.body) <= MAX_ENEMY_LENGTH and next_pos != previous_head
        if not should_grow:
            enemy.body.popleft()
        for segment in enemy.body:
            occupied.add((segment.x, segment.y))
        resolve_enemy_effects(run, enemy, save)
        if run.game_over:
            return


def decay_effects(run: RunState) -> None:
    if run.blind_ticks > 0:
        run.blind_ticks -= 1
    if run.ping_ticks > 0:
        run.ping_ticks -= 1
        if run.ping_ticks == 0:
            run.ping_path.clear()
    for segment in run.body:
        if segment.infected > 0:
            segment.infected -= 1


def tick(run: RunState, save: SaveData, rng: random.Random, *, player_action: Callable[[], None] | None, reason: str) -> None:
    if run.game_over:
        return
    if player_action is not None:
        player_action()
    if run.game_over:
        return
    update_hazards(run)
    if run.game_over:
        return
    advance_enemies(run, save, rng, grow=reason == "key")
    update_hazards(run)
    decay_effects(run)
    run.ticks += 1
    if reason == "idle" and not run.game_over:
        run.log("Hardcore clock ticked.")


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


def enemy_head_color(theme: dict[str, tuple[int, int, int] | str], enemy: Enemy) -> tuple[int, int, int]:
    if enemy.kind == "virus":
        return mix(theme["enemy"], theme["bytes"], 0.35)
    if enemy.kind == "blinder":
        return mix(theme["enemy"], theme["player"], 0.2)
    if enemy.kind == "fuse":
        return theme["enemy_alt"]
    return theme["enemy"]


def enemy_segment_color(
    theme: dict[str, tuple[int, int, int] | str],
    enemy: Enemy,
    index: int,
) -> tuple[int, int, int]:
    head_color = enemy_head_color(theme, enemy)
    length = max(1, len(enemy.body) - 1)
    intensity = 0.18 + 0.82 * (index / length)
    return mix(theme["floor"], head_color, intensity)


def update_camera(run: RunState, viewport_w: int, viewport_h: int) -> tuple[int, int]:
    head_x, head_y = run.head.x, run.head.y
    if run.camera_left is None or run.camera_top is None:
        run.camera_left = head_x - viewport_w // 2
        run.camera_top = head_y - viewport_h // 2
        return run.camera_left, run.camera_top

    margin_x = max(8, viewport_w // 5)
    margin_y = max(4, viewport_h // 5)
    left = run.camera_left
    top = run.camera_top

    if head_x < left + margin_x:
        left = head_x - margin_x
    elif head_x >= left + viewport_w - margin_x:
        left = head_x - viewport_w + margin_x + 1

    if head_y < top + margin_y:
        top = head_y - margin_y
    elif head_y >= top + viewport_h - margin_y:
        top = head_y - viewport_h + margin_y + 1

    run.camera_left = left
    run.camera_top = top
    return left, top


class ViskApp:
    def __init__(self) -> None:
        self.save = load_save()
        self.rng = random.Random()
        self.state = "menu"
        self.run: RunState | None = None
        self.shop_cursor = 0
        self.last_canvas: Canvas | None = None
        self.run_static_canvas: Canvas | None = None
        self.run_overlay_prev: dict[tuple[int, int], Cell] = {}
        self.run_camera_key: tuple[int, int, int, int, int] | None = None

    def new_run(self) -> None:
        self.run = create_run(self.save)
        self.state = "run"
        self.run_static_canvas = None
        self.run_overlay_prev = {}
        self.run_camera_key = None

    def finish_run(self) -> None:
        if self.run is None:
            return
        if self.run.extracted:
            reward = self.run.bytes_collected + 40 * self.run.kills
            self.save.banked_bytes += reward
            self.save.streak += 1
        else:
            self.save.streak = 0
        save_save(self.save)
        self.state = "result"
        self.run_static_canvas = None
        self.run_overlay_prev = {}
        self.run_camera_key = None

    def handle_menu_key(self, key: str) -> None:
        if key in ("n", "N", "ENTER"):
            self.new_run()
        elif key in ("s", "S"):
            self.state = "shop"
        elif key in ("h", "H"):
            self.save.hardcore = not self.save.hardcore
            save_save(self.save)
        elif key in ("q", "Q", "ESC"):
            raise SystemExit

    def handle_shop_key(self, key: str) -> None:
        if key == "UP":
            self.shop_cursor = (self.shop_cursor - 1) % len(SHOP_ITEMS)
        elif key == "DOWN":
            self.shop_cursor = (self.shop_cursor + 1) % len(SHOP_ITEMS)
        elif key in ("ENTER", " "):
            self.purchase_selected()
        elif key in ("m", "M", "ESC"):
            self.state = "menu"

    def purchase_selected(self) -> None:
        item = SHOP_ITEMS[self.shop_cursor]
        level = self.save.upgrades[item["id"]]
        cost = item["base_cost"] + level * 90
        if self.save.banked_bytes < cost:
            return
        self.save.banked_bytes -= cost
        self.save.upgrades[item["id"]] += 1
        save_save(self.save)

    def handle_result_key(self, key: str) -> None:
        if key in ("ENTER", "n", "N"):
            self.new_run()
        elif key in ("m", "M", "ESC"):
            self.state = "menu"

    def execute_direction_command(self, direction: str) -> None:
        if self.run is None or self.run.game_over:
            return
        self.run.direction = direction
        self.run.pending_command = ""
        self.run.log(f"Direction changed to {direction.upper()}.")
        for ch in direction:
            if self.run.game_over:
                return
            tick(
                self.run,
                self.save,
                self.rng,
                player_action=lambda ch=ch: advance_player_with_mode(self.run, ch, record_command=False),
                reason="key",
            )

    def handle_run_key(self, key: str) -> None:
        if self.run is None or self.run.game_over:
            return
        if key == "ESC":
            self.state = "menu"
            return
        if key == "BACKSPACE":
            if self.run.pending_command:
                self.run.pending_command = self.run.pending_command[:-1]
            return
        if key == "ENTER":
            command = self.run.pending_command.strip().lower()
            if command in DIRECTIONS:
                self.execute_direction_command(command)
            elif command:
                tick(self.run, self.save, self.rng, player_action=lambda: submit_command(self.run), reason="key")
            return
        if len(key) == 1 and key.isprintable():
            self.run.pending_command += key

    def render(self) -> Canvas:
        cols, rows = shutil.get_terminal_size((120, 38))
        cols = max(cols, 60)
        rows = max(rows, 22)
        if self.state == "menu":
            return self.render_menu(cols, rows)
        if self.state == "shop":
            return self.render_shop(cols, rows)
        if self.state == "result":
            return self.render_result(cols, rows)
        return self.render_run(cols, rows)

    def draw_panel(
        self,
        canvas: Canvas,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        title: str,
        theme: dict[str, tuple[int, int, int] | str],
    ) -> None:
        bg_color = theme["bg_alt"]
        wall = theme["wall"]
        for yy in range(y, y + height):
            for xx in range(x, x + width):
                canvas.put(xx, yy, " ", bg_color=bg_color)
        for xx in range(x, x + width):
            canvas.put(xx, y, "─", fg_color=wall, bg_color=bg_color)
            canvas.put(xx, y + height - 1, "─", fg_color=wall, bg_color=bg_color)
        for yy in range(y, y + height):
            canvas.put(x, yy, "│", fg_color=wall, bg_color=bg_color)
            canvas.put(x + width - 1, yy, "│", fg_color=wall, bg_color=bg_color)
        for corner in ((x, y), (x + width - 1, y), (x, y + height - 1), (x + width - 1, y + height - 1)):
            canvas.put(corner[0], corner[1], "┼", fg_color=wall, bg_color=bg_color, bold=True)
        canvas.text(x + 2, y, f" {title} ", fg_color=theme["pickup"], bg_color=bg_color, bold=True)

    def build_run_static_canvas(self, run: RunState, cols: int, rows: int, left: int, top: int) -> Canvas:
        theme = run.sector.theme
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=run.sector.seed)
        for sx in range(cols):
            wx = left + sx
            for sy in range(rows):
                wy = top + sy
                base = theme["floor"]
                if (wx, wy) in run.sector.walls:
                    glyph = "#" if (wx + wy) % 5 else "+"
                    canvas.put(sx, sy, glyph, fg_color=theme["wall"], bg_color=base, bold=False)
                else:
                    noise = hash_noise(wx, wy, run.sector.seed)
                    if noise % 73 == 0:
                        canvas.put(sx, sy, ".", fg_color=theme["muted"], bg_color=base)
                    else:
                        canvas.put(sx, sy, " ", bg_color=base)
        return canvas

    def build_run_overlay(
        self,
        run: RunState,
        cols: int,
        rows: int,
        left: int,
        top: int,
    ) -> dict[tuple[int, int], Cell]:
        theme = run.sector.theme
        overlay: dict[tuple[int, int], Cell] = {}

        def on_screen(wx: int, wy: int) -> tuple[int, int] | None:
            sx = wx - left
            sy = wy - top
            if 0 <= sx < cols and 0 <= sy < rows:
                return sx, sy
            return None

        def put(x: int, y: int, ch: str, fg_color=None, bg_color=None, bold: bool = False) -> None:
            if 0 <= x < cols and 0 <= y < rows and ch:
                overlay[(x, y)] = Cell(ch[0], fg_color, bg_color if bg_color is not None else theme["floor"], bold)

        def text(x: int, y: int, value: str, fg_color=None, bg_color=None, bold: bool = False) -> None:
            for idx, ch in enumerate(value):
                put(x + idx, y, ch, fg_color=fg_color, bg_color=bg_color, bold=bold)

        if run.ping_ticks > 0:
            for wx, wy in run.ping_path:
                if screen := on_screen(wx, wy):
                    put(screen[0], screen[1], "·", fg_color=theme["ping"], bg_color=theme["floor"])

        ex, ey = run.sector.exit
        if screen := on_screen(ex, ey):
            pulse = 0.25 + 0.25 * (math.sin(time.monotonic() * 3.5) + 1) / 2
            exit_color = mix(theme["pickup"], theme["accent"], pulse)
            put(screen[0], screen[1], "x", fg_color=exit_color, bg_color=theme["floor"])

        for shard in run.sector.byte_shards:
            if screen := on_screen(shard.x, shard.y):
                put(screen[0], screen[1], "$", fg_color=theme["bytes"], bg_color=theme["floor"])

        for pickup in run.sector.pickups:
            if pickup.resolved:
                continue
            color = theme["enemy_alt"] if pickup.failed else theme["pickup"]
            text_value = pickup.text if not pickup.failed else "." * len(pickup.text)
            for i, ch in enumerate(text_value):
                if screen := on_screen(pickup.x + i, pickup.y):
                    put(screen[0], screen[1], ch, fg_color=color, bg_color=theme["floor"])

        for bomb in run.bombs:
            if screen := on_screen(bomb.x, bomb.y):
                put(screen[0], screen[1], str(max(1, bomb.fuse)), fg_color=theme["enemy"], bg_color=theme["floor"])
        for mine in run.mines:
            if screen := on_screen(mine.x, mine.y):
                put(screen[0], screen[1], "^", fg_color=theme["accent"], bg_color=theme["floor"])

        for enemy in run.sector.enemies:
            if enemy.dead:
                continue
            for idx, segment in enumerate(enemy.body):
                if screen := on_screen(segment.x, segment.y):
                    put(
                        screen[0],
                        screen[1],
                        segment.ch,
                        fg_color=enemy_segment_color(theme, enemy, idx),
                        bg_color=theme["floor"],
                    )

        blink = int(time.monotonic() * 2.6) % 2 == 0
        for enemy in run.sector.enemies:
            if enemy.dead:
                continue
            head_color = enemy_head_color(theme, enemy)
            if screen := on_screen(enemy.head.x, enemy.head.y):
                flash_fg, flash_bg = invert_colors(head_color, theme["floor"], blink)
                put(screen[0], screen[1], enemy.head.ch, fg_color=flash_fg, bg_color=flash_bg)

        for idx, segment in enumerate(run.body):
            if screen := on_screen(segment.x, segment.y):
                color = theme["player"]
                if segment.infected > 0:
                    color = mix(color, theme["enemy"], 0.6)
                put(screen[0], screen[1], segment.ch or " ", fg_color=color, bg_color=theme["floor"])

        if screen := on_screen(run.head.x, run.head.y):
            cursor_fg, cursor_bg = invert_colors(theme["player"], theme["floor"], blink)
            put(screen[0], screen[1], arrow_for_direction(run.direction), fg_color=cursor_fg, bg_color=cursor_bg)

        if cols >= 56:
            bytes_text = f"b{run.bytes_collected}"
            text(max(1, cols - len(bytes_text) - 2), 1, bytes_text, fg_color=theme["muted"])
        if run.pending_command and rows >= 3:
            command_text = f"cmd> {run.pending_command}"
            text(2, rows - 2, command_text[: max(0, cols // 2)], fg_color=theme["player_pending"])
        if cols >= 72:
            active_cache = " ".join(f"{name}:{run.inventory.get(name, 0)}" for name in ABILITY_NAMES if run.inventory.get(name, 0))
            if active_cache:
                text(max(1, cols - len(active_cache) - 2), rows - 2, active_cache[: max(0, cols - 4)], fg_color=theme["muted"])
        if run.extracted:
            text(max(2, cols // 2 - 5), 2, "extracting", fg_color=theme["pickup"])
        elif run.game_over:
            text(max(2, cols // 2 - 5), 2, "terminated", fg_color=theme["enemy"])

        return overlay

    def compose_run_canvas(self, base_canvas: Canvas, overlay: dict[tuple[int, int], Cell]) -> Canvas:
        full = Canvas(base_canvas.width, base_canvas.height, (0, 0, 0))
        full.cells = [[Cell(cell.ch, cell.fg, cell.bg, cell.bold) for cell in row] for row in base_canvas.cells]
        for (x, y), cell in overlay.items():
            full.cells[y][x] = Cell(cell.ch, cell.fg, cell.bg, cell.bold)
        return full

    def apply_overlay_to_canvas(
        self,
        base_canvas: Canvas,
        target_canvas: Canvas | None,
        previous: dict[tuple[int, int], Cell],
        current: dict[tuple[int, int], Cell],
    ) -> Canvas:
        if target_canvas is None or target_canvas.width != base_canvas.width or target_canvas.height != base_canvas.height:
            return self.compose_run_canvas(base_canvas, current)
        for x, y in set(previous) | set(current):
            cell = current.get((x, y))
            if cell is None:
                base_cell = base_canvas.cells[y][x]
                target_canvas.cells[y][x] = Cell(base_cell.ch, base_cell.fg, base_cell.bg, base_cell.bold)
            else:
                target_canvas.cells[y][x] = Cell(cell.ch, cell.fg, cell.bg, cell.bold)
        return target_canvas

    def render_sparse_overlay_diff(
        self,
        base_canvas: Canvas,
        previous: dict[tuple[int, int], Cell],
        current: dict[tuple[int, int], Cell],
    ) -> str:
        positions = sorted(set(previous) | set(current), key=lambda pos: (pos[1], pos[0]))
        if not positions:
            return ""
        parts: list[str] = []
        current_style: tuple[tuple[int, int, int] | None, tuple[int, int, int] | None, bool] | None = None
        for x, y in positions:
            old_cell = previous.get((x, y))
            new_cell = current.get((x, y))
            if old_cell == new_cell:
                continue
            cell = new_cell if new_cell is not None else base_canvas.cells[y][x]
            parts.append(f"\x1b[{y + 1};{x + 1}H")
            style = (cell.fg, cell.bg, cell.bold)
            if style != current_style:
                parts.append(style_reset())
                if cell.bold:
                    parts.append("\x1b[1m")
                if cell.fg:
                    parts.append(fg(cell.fg))
                if cell.bg:
                    parts.append(bg(cell.bg))
                current_style = style
            parts.append(cell.ch)
        if not parts:
            return ""
        parts.append(style_reset())
        return "".join(parts)

    def render_menu(self, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=2311)
        top = max(2, rows // 2 - 12)
        art_width = max(len(line) for line in RUN_ART)
        left = max(2, (cols - art_width) // 2)
        for i, line in enumerate(RUN_ART):
            canvas.text(left, top + i, line, fg_color=mix(theme["accent"], theme["player"], i / max(1, len(RUN_ART) - 1)), bold=True)
        summary = "Minimalist terminal survival. Type a command, press ENTER to submit, and move by entering up/down/left/right."
        for idx, line in enumerate(wrap_lines(summary, max(30, cols - 12))):
            canvas.text(6, top + 8 + idx, line, fg_color=theme["muted"])
        options = [
            "[N] New Run",
            "[S] Shop",
            f"[H] Hardcore: {'ON' if self.save.hardcore else 'OFF'}",
            "[Q] Quit",
        ]
        for idx, option in enumerate(options):
            canvas.text(8, top + 12 + idx * 2, option, fg_color=theme["player"] if idx != 2 else theme["pickup"], bold=True)
        stats = [
            f"Banked bytes: {self.save.banked_bytes}",
            f"Win streak: {self.save.streak}",
            f"Upgrades: DASH {self.save.upgrades['dash_cache']} | PING {self.save.upgrades['ping_cache']} | MAGNET {self.save.upgrades['magnet']} | FOCUS {self.save.upgrades['focus']}",
        ]
        for idx, line in enumerate(stats):
            canvas.text(6, rows - 5 + idx, line[: cols - 10], fg_color=theme["accent"])
        return canvas

    def render_shop(self, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=7001)
        panel_w = min(cols - 6, 90)
        panel_h = min(rows - 4, 20)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.draw_panel(canvas, x, y, panel_w, panel_h, title="BYTE MARKET", theme=theme)
        canvas.text(x + 3, y + 2, f"Banked bytes: {self.save.banked_bytes}", fg_color=theme["player"], bold=True)
        canvas.text(x + 3, y + 3, "Press ENTER to buy. ESC to return.", fg_color=theme["muted"])
        row_y = y + 5
        for idx, item in enumerate(SHOP_ITEMS):
            level = self.save.upgrades[item["id"]]
            cost = item["base_cost"] + level * 90
            selected = idx == self.shop_cursor
            tone = theme["pickup"] if selected else theme["player"]
            prefix = ">" if selected else " "
            canvas.text(x + 3, row_y, f"{prefix} {item['name']}  L{level}  COST {cost}", fg_color=tone, bold=selected)
            for line_idx, line in enumerate(wrap_lines(item["description"], panel_w - 10)):
                canvas.text(x + 6, row_y + 1 + line_idx, line, fg_color=theme["muted"])
            row_y += 4
        return canvas

    def render_result(self, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=415)
        panel_w = min(cols - 8, 84)
        panel_h = min(rows - 6, 18)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.draw_panel(canvas, x, y, panel_w, panel_h, title="RUN RESULT", theme=theme)
        if self.run is None:
            return canvas
        title = "EXTRACTION COMPLETE" if self.run.extracted else "SESSION TERMINATED"
        tone = theme["accent"] if self.run.extracted else theme["enemy"]
        canvas.text(x + 3, y + 2, title, fg_color=tone, bold=True)
        if self.run.extracted:
            reward = self.run.bytes_collected + 40 * self.run.kills
            lines = [f"Bytes banked: {reward}", f"Enemies deleted: {self.run.kills}", f"Streak: {self.save.streak}"]
        else:
            lines = [f"Cause: {self.run.cause}", f"Bytes lost: {self.run.bytes_collected}", f"Streak reset to: {self.save.streak}"]
        for idx, line in enumerate(lines):
            canvas.text(x + 3, y + 5 + idx * 2, line, fg_color=theme["player"])
        canvas.text(x + 3, y + panel_h - 3, "[ENTER] new run   [M] menu", fg_color=theme["pickup"], bold=True)
        return canvas

    def render_run(self, cols: int, rows: int) -> Canvas:
        run = self.run
        if run is None:
            return self.render_menu(cols, rows)
        theme = run.sector.theme
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=run.sector.seed)

        viewport_w = cols
        viewport_h = rows
        if rows < 14 or cols < 48:
            canvas.text(2, 2, "Window too small. Expand the terminal to continue the run.", fg_color=theme["pickup"])
            return canvas

        head = run.head
        left, top = update_camera(run, viewport_w, viewport_h)
        offset_x = 0
        offset_y = 0
        ensure_generated_rect(run.sector, left, top, viewport_w, viewport_h)

        def on_screen(wx: int, wy: int) -> tuple[int, int] | None:
            sx = wx - left + offset_x
            sy = wy - top + offset_y
            if offset_x <= sx < offset_x + viewport_w and offset_y <= sy < offset_y + viewport_h:
                return sx, sy
            return None

        for wx in range(left, left + viewport_w):
            for wy in range(top, top + viewport_h):
                screen = on_screen(wx, wy)
                if screen is None:
                    continue
                base = theme["floor"]
                if (wx, wy) in run.sector.walls:
                    glyph = "#" if (wx + wy) % 5 else "+"
                    canvas.put(screen[0], screen[1], glyph, fg_color=theme["wall"], bg_color=base, bold=False)
                else:
                    noise = hash_noise(wx, wy, run.sector.seed)
                    if noise % 73 == 0:
                        canvas.put(screen[0], screen[1], ".", fg_color=theme["muted"], bg_color=base)
                    else:
                        canvas.put(screen[0], screen[1], " ", bg_color=base)

        if run.ping_ticks > 0:
            for wx, wy in run.ping_path:
                screen = on_screen(wx, wy)
                if screen:
                    canvas.put(screen[0], screen[1], "·", fg_color=theme["ping"], bg_color=theme["floor"])

        ex, ey = run.sector.exit
        if screen := on_screen(ex, ey):
            pulse = 0.25 + 0.25 * (math.sin(time.monotonic() * 3.5) + 1) / 2
            exit_color = mix(theme["pickup"], theme["accent"], pulse)
            canvas.put(screen[0], screen[1], "x", fg_color=exit_color, bg_color=theme["floor"], bold=False)

        for shard in run.sector.byte_shards:
            if screen := on_screen(shard.x, shard.y):
                canvas.put(screen[0], screen[1], "$", fg_color=theme["bytes"], bg_color=theme["floor"], bold=False)

        for pickup in run.sector.pickups:
            if pickup.resolved:
                continue
            color = theme["enemy_alt"] if pickup.failed else theme["pickup"]
            text = pickup.text if not pickup.failed else "." * len(pickup.text)
            for i, ch in enumerate(text):
                if screen := on_screen(pickup.x + i, pickup.y):
                    canvas.put(screen[0], screen[1], ch, fg_color=color, bg_color=theme["floor"], bold=False)

        for bomb in run.bombs:
            if screen := on_screen(bomb.x, bomb.y):
                canvas.put(screen[0], screen[1], str(max(1, bomb.fuse)), fg_color=theme["enemy"], bg_color=theme["floor"], bold=False)
        for mine in run.mines:
            if screen := on_screen(mine.x, mine.y):
                canvas.put(screen[0], screen[1], "^", fg_color=theme["accent"], bg_color=theme["floor"], bold=False)

        for enemy in run.sector.enemies:
            if enemy.dead:
                continue
            for idx, segment in enumerate(enemy.body):
                if screen := on_screen(segment.x, segment.y):
                    canvas.put(
                        screen[0],
                        screen[1],
                        segment.ch,
                        fg_color=enemy_segment_color(theme, enemy, idx),
                        bg_color=theme["floor"],
                        bold=False,
                    )

        blink = int(time.monotonic() * 2.6) % 2 == 0
        for enemy in run.sector.enemies:
            if enemy.dead:
                continue
            head_color = enemy_head_color(theme, enemy)
            if screen := on_screen(enemy.head.x, enemy.head.y):
                flash_fg, flash_bg = invert_colors(head_color, theme["floor"], blink)
                canvas.put(
                    screen[0],
                    screen[1],
                    enemy.head.ch,
                    fg_color=flash_fg,
                    bg_color=flash_bg,
                    bold=False,
                )

        for idx, segment in enumerate(run.body):
            if screen := on_screen(segment.x, segment.y):
                color = theme["player"]
                if segment.infected > 0:
                    color = mix(color, theme["enemy"], 0.6)
                canvas.put(screen[0], screen[1], segment.ch or " ", fg_color=color, bg_color=theme["floor"], bold=False)

        if screen := on_screen(run.head.x, run.head.y):
            cursor_fg, cursor_bg = invert_colors(theme["player"], theme["floor"], blink)
            canvas.put(
                screen[0],
                screen[1],
                arrow_for_direction(run.direction),
                fg_color=cursor_fg,
                bg_color=cursor_bg,
                bold=False,
            )

        if cols >= 56:
            bytes_text = f"b{run.bytes_collected}"
            canvas.text(max(1, cols - len(bytes_text) - 2), 1, bytes_text, fg_color=theme["muted"])
        if run.pending_command and rows >= 3:
            command_text = f"cmd> {run.pending_command}"
            canvas.text(2, rows - 2, command_text[: max(0, cols // 2)], fg_color=theme["player_pending"])
        if cols >= 72:
            active_cache = " ".join(f"{name}:{run.inventory.get(name, 0)}" for name in ABILITY_NAMES if run.inventory.get(name, 0))
            if active_cache:
                canvas.text(max(1, cols - len(active_cache) - 2), rows - 2, active_cache[: max(0, cols - 4)], fg_color=theme["muted"])
        if run.extracted:
            canvas.text(max(2, cols // 2 - 5), 2, "extracting", fg_color=theme["pickup"])
        elif run.game_over:
            canvas.text(max(2, cols // 2 - 5), 2, "terminated", fg_color=theme["enemy"])

        if run.blind_ticks > 0:
            blind_tint = theme["blind"]
            for row in range(offset_y, offset_y + viewport_h):
                for col in range(offset_x, offset_x + viewport_w):
                    cell = canvas.cells[row][col]
                    cell.bg = mix(cell.bg or blind_tint, blind_tint, 0.72)
                    if hash_noise(col, row, run.ticks + run.blind_ticks) % 4 != 0:
                        cell.ch = " "
            canvas.text(2, 2, "feed corrupted", fg_color=theme["muted"], bg_color=blind_tint, bold=False)

        return canvas

    def run_loop(self) -> None:
        with TerminalController() as terminal:
            while True:
                cols, rows = shutil.get_terminal_size((120, 38))
                cols = max(cols, 60)
                rows = max(rows, 22)

                if self.state == "run" and self.run is not None and self.run.blind_ticks <= 0 and rows >= 14 and cols >= 48:
                    left, top = update_camera(self.run, cols, rows)
                    ensure_generated_rect(self.run.sector, left, top, cols, rows)
                    camera_key = (cols, rows, left, top, self.run.sector.seed)
                    if self.run_static_canvas is None or self.run_camera_key != camera_key:
                        self.run_camera_key = camera_key
                        self.run_static_canvas = self.build_run_static_canvas(self.run, cols, rows, left, top)
                        self.run_overlay_prev = self.build_run_overlay(self.run, cols, rows, left, top)
                        full = self.compose_run_canvas(self.run_static_canvas, self.run_overlay_prev)
                        frame = full.render_diff(self.last_canvas)
                        self.last_canvas = full
                    else:
                        overlay = self.build_run_overlay(self.run, cols, rows, left, top)
                        frame = self.render_sparse_overlay_diff(self.run_static_canvas, self.run_overlay_prev, overlay)
                        self.last_canvas = self.apply_overlay_to_canvas(
                            self.run_static_canvas,
                            self.last_canvas,
                            self.run_overlay_prev,
                            overlay,
                        )
                        self.run_overlay_prev = overlay
                    if frame:
                        sys.stdout.write(frame)
                        sys.stdout.flush()
                else:
                    canvas = self.render()
                    frame = canvas.render_diff(self.last_canvas)
                    if frame:
                        sys.stdout.write(frame)
                        sys.stdout.flush()
                    self.last_canvas = canvas
                    if self.state != "run":
                        self.run_static_canvas = None
                        self.run_overlay_prev = {}
                        self.run_camera_key = None

                timeout = 0.08
                if self.state == "run" and self.run is not None and not self.run.game_over and self.run.hardcore:
                    timeout = 0.45
                elif self.state == "run":
                    timeout = 0.12
                key = terminal.read_key(timeout)
                if key is None:
                    if self.state == "run" and self.run is not None and not self.run.game_over and self.run.hardcore:
                        tick(self.run, self.save, self.rng, player_action=None, reason="idle")
                    continue
                if self.state == "menu":
                    self.handle_menu_key(key)
                elif self.state == "shop":
                    self.handle_shop_key(key)
                elif self.state == "result":
                    self.handle_result_key(key)
                elif self.state == "run":
                    self.handle_run_key(key)
                    if self.run is not None and self.run.game_over:
                        self.finish_run()

    def smoke_test(self) -> None:
        self.new_run()
        assert self.run is not None
        for char in "right":
            advance_player(self.run, char)
        submit_command(self.run)
        use_ability(self.run, "ping")
        frame = self.render_run(120, 40).render_full()
        print("VISK smoke test ok")
        print(frame[:400].replace("\x1b", "<ESC>"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VISK terminal roguelite")
    parser.add_argument("--smoke-test", action="store_true", help="run a non-interactive smoke test")
    args = parser.parse_args(argv)
    app = ViskApp()
    if args.smoke_test:
        app.smoke_test()
        return 0
    try:
        app.run_loop()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
