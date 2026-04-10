from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .constants import CHUNK_SIZE, NOISE_GLYPHS
from .utils import bg, fg, hash_noise, mix, style_reset


Color = tuple[int, int, int]
Theme = dict[str, Color | str]


@dataclass
class Cell:
    ch: str = " "
    fg: Color | None = None
    bg: Color | None = None
    bold: bool = False

    def is_transparent(self) -> bool:
        return self.ch == " " and self.fg is None and self.bg is None and not self.bold


class Canvas:
    def __init__(self, width: int, height: int, background: Color | None) -> None:
        self.width = width
        self.height = height
        self.cells = [
            [Cell(" ", None, background, False) for _ in range(width)]
            for _ in range(height)
        ]

    @classmethod
    def transparent(cls, width: int, height: int) -> "Canvas":
        return cls(width, height, None)

    def copy(self) -> "Canvas":
        clone = Canvas.transparent(self.width, self.height)
        clone.cells = [
            [Cell(cell.ch, cell.fg, cell.bg, cell.bold) for cell in row]
            for row in self.cells
        ]
        return clone

    def put(
        self,
        x: int,
        y: int,
        ch: str,
        *,
        fg_color: Color | None = None,
        bg_color: Color | None = None,
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
        fg_color: Color | None = None,
        bg_color: Color | None = None,
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
        base: Color,
        alt: Color,
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
        current_style: tuple[Color | None, Color | None, bool] | None = None
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
class Debris:
    x: int
    y: int
    ch: str
    origin: str
    created_at: float = 0.0
    fade_duration: float = 7.5


@dataclass
class Pickup:
    x: int
    y: int
    text: str
    ability: str
    failed: bool = False
    resolved: bool = False
    matched_indices: set[int] = field(default_factory=set)
    error_indices: set[int] = field(default_factory=set)

    def cells(self) -> list[tuple[int, int]]:
        return [(self.x + i, self.y) for i in range(len(self.text))]


@dataclass
class ByteShard:
    x: int
    y: int
    value: int


@dataclass
class Bomb:
    object_id: str
    x: int
    y: int
    fuse: int
    radius: int
    owner: str = "player"
    cells: tuple[tuple[int, int], ...] = ()

    def display_cells(self) -> list[tuple[int, int, str]]:
        countdown = str(max(0, self.fuse))
        if len(self.cells) == 4:
            glyphs = ("b", countdown, "m", "b")
            return [(cell[0], cell[1], glyph) for cell, glyph in zip(self.cells, glyphs)]
        return [(self.x, self.y, countdown)]

    def advance(self, *, advance_fuse: bool) -> bool:
        if advance_fuse:
            self.fuse -= 1
        return self.fuse < 0

    def render(self, on_screen, put, theme) -> None:
        tone = mix(theme["enemy"], (255, 150, 150), 0.25)
        for wx, wy, ch in self.display_cells():
            if screen := on_screen(wx, wy):
                put(screen[0], screen[1], ch, fg_color=tone, bold=True)


@dataclass
class Mine:
    object_id: str
    x: int
    y: int
    trigger_radius: int = 3
    explosion_radius: int = 3

    def triggered_by(self, enemy_positions: set[tuple[int, int]], manhattan) -> bool:
        return any(
            manhattan((self.x, self.y), position) <= self.trigger_radius
            for position in enemy_positions
        )

    def render(self, on_screen, put, theme, *, blink: bool, invert_colors) -> None:
        if screen := on_screen(self.x, self.y):
            base_color = theme["player"]
            flash_color = (255, 96, 96)
            put(
                screen[0],
                screen[1],
                "i",
                fg_color=flash_color if blink else base_color,
                bold=True,
            )


@dataclass
class PingTrace:
    object_id: str
    path: list[tuple[int, int]]
    ticks_remaining: int

    def advance(self) -> bool:
        self.ticks_remaining -= 1
        return self.ticks_remaining <= 0

    def render(self, on_screen, put, theme) -> None:
        for wx, wy in self.path:
            if screen := on_screen(wx, wy):
                put(screen[0], screen[1], "·", fg_color=theme["ping"])


@dataclass
class UndoAction:
    kind: str
    object_id: str | None = None
    inventory_name: str | None = None
    label: str | None = None


@dataclass
class CommandUndo:
    step_index: int
    previous_pending: str
    undo_action: UndoAction | None = None


@dataclass
class ExplosionParticle:
    start_dx: float
    start_dy: float
    velocity_x: float
    velocity_y: float
    spawn_delay: float
    lifetime: float
    flicker_hz: float
    phase: int
    shade: int


@dataclass
class ExplosionEffect:
    x: int
    y: int
    radius: int
    started_at: float
    duration: float
    particles: list[ExplosionParticle] = field(default_factory=list)


@dataclass
class Enemy:
    kind: str
    body: deque[Segment]
    heading: tuple[int, int]
    speed_bias: float = 0.75
    fuse_timer: int = 0
    stunned: int = 0
    last_move_mode: str = "init"
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
class ExtractAttempt:
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
    theme: Theme
    start: tuple[int, int]
    generated_chunks: set[tuple[int, int]]
    chunk_size: int = CHUNK_SIZE
    terrain_revision: int = 0


@dataclass
class SaveData:
    banked_bytes: int = 0
    streak: int = 0
    hardcore: bool = False
    audio_enabled: bool = True
    upgrades: dict[str, int] = field(
        default_factory=lambda: {
            "dash_cache": 0,
            "ping_cache": 0,
            "magnet": 0,
            "focus": 0,
        }
    )


@dataclass
class CreditsState:
    body: deque[Segment]
    direction: str = "right"
    pending_command: str = ""
    direction_undos: list[tuple[int, str, str]] = field(default_factory=list)
    width: int = 44
    height: int = 14

    @property
    def head(self) -> Segment:
        return self.body[-1]


@dataclass
class RunState:
    sector: Sector
    body: deque[Segment]
    direction: str
    pending_command: str = ""
    direction_undos: list[tuple[int, str, str]] = field(default_factory=list)
    command_undos: list[CommandUndo] = field(default_factory=list)
    bytes_collected: int = 0
    inventory: dict[str, int] = field(default_factory=dict)
    pings: list[PingTrace] = field(default_factory=list)
    blind_ticks: int = 0
    silence_ticks: int = 0
    bombs: list[Bomb] = field(default_factory=list)
    mines: list[Mine] = field(default_factory=list)
    wreckage: list[Debris] = field(default_factory=list)
    explosions: list[ExplosionEffect] = field(default_factory=list)
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=6))
    pickup_attempt: PickupAttempt | None = None
    extract_attempt: ExtractAttempt | None = None
    extract_matched_indices: set[int] = field(default_factory=set)
    game_over: bool = False
    extracted: bool = False
    cause: str = ""
    ticks: int = 0
    kills: int = 0
    hardcore: bool = False
    camera_left: int | None = None
    camera_top: int | None = None
    next_object_id: int = 0

    @property
    def head(self) -> Segment:
        return self.body[-1]

    def log(self, message: str) -> None:
        self.messages.appendleft(message)
