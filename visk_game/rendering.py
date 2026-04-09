from __future__ import annotations

import math
import time

from .constants import ABILITY_NAMES, CREDITS_PAGE_LINES, RUN_ART, SHOP_ITEMS, THEMES
from .gameplay import active_enemies_for_run, active_explosions
from .generation import ensure_generated_rect
from .models import (
    Bomb,
    Canvas,
    Cell,
    CreditsState,
    Debris,
    Enemy,
    ExplosionEffect,
    Pickup,
    RunState,
    SaveData,
    Theme,
)
from .utils import (
    arrow_for_direction,
    bg,
    clamp,
    fg,
    hash_noise,
    invert_colors,
    mix,
    style_reset,
    wrap_lines,
)


def debris_color(theme: Theme, piece: Debris, *, now: float) -> tuple[int, int, int]:
    if piece.origin == "player":
        base_color = mix(theme["player"], theme["muted"], 0.55)
    else:
        base_color = mix(theme["enemy"], theme["wall"], 0.45)
    age = max(0.0, now - piece.created_at)
    fade = max(0.0, min(1.0, age / max(0.001, piece.fade_duration)))
    return mix(base_color, theme["wall"], fade)


def pickup_letter_color(
    theme: Theme, pickup: Pickup, index: int
) -> tuple[int, int, int]:
    if index in pickup.error_indices:
        return theme["enemy"]
    if index in pickup.matched_indices:
        return mix(theme["bytes"], theme["player"], 0.3)
    if pickup.failed:
        return mix(theme["enemy"], theme["muted"], 0.35)
    return theme["pickup"]


def pickup_color_at_position(
    theme: Theme,
    run: RunState,
    position: tuple[int, int],
) -> tuple[int, int, int] | None:
    for pickup in run.sector.pickups:
        if pickup.resolved:
            continue
        for index, cell in enumerate(pickup.cells()):
            if cell != position:
                continue
            if (
                index in pickup.matched_indices
                or index in pickup.error_indices
                or pickup.failed
            ):
                return pickup_letter_color(theme, pickup, index)
            return None
    return None


def player_segment_color(
    theme: Theme,
    index: int,
    body_length: int,
    *,
    infected: bool,
    pickup_color: tuple[int, int, int] | None = None,
) -> tuple[int, int, int]:
    if pickup_color is not None:
        color = pickup_color
    else:
        fade_cap = 0.78
        if body_length <= 1:
            color = theme["player"]
        else:
            recentness = index / max(1, body_length - 1)
            fade_amount = (1.0 - recentness) * fade_cap
            color = mix(theme["player"], theme["muted"], fade_amount)
    if infected:
        color = mix(color, theme["enemy"], 0.6)
    return color


def bomb_display_cells(bomb: Bomb) -> list[tuple[int, int, str]]:
    countdown = str(max(0, bomb.fuse))
    if len(bomb.cells) == 4:
        glyphs = ("b", countdown, "m", "b")
        return [(cell[0], cell[1], glyph) for cell, glyph in zip(bomb.cells, glyphs)]
    return [(bomb.x, bomb.y, countdown)]


def bomb_text_color(theme: Theme) -> tuple[int, int, int]:
    return mix(theme["enemy"], (255, 150, 150), 0.25)


EXPLOSION_BLOCKS = ("\u2591", "\u2592", "\u2593", "\u2588")


def explosion_particle_visuals(
    effect: ExplosionEffect,
    tick_seed: int,
    *,
    now: float,
) -> dict[tuple[int, int], tuple[str, tuple[int, int, int], tuple[int, int, int]]]:
    elapsed = max(0.0, now - effect.started_at)
    effect_seed = tick_seed + int(effect.started_at * 1000)
    visuals: dict[
        tuple[int, int], tuple[float, str, tuple[int, int, int], tuple[int, int, int]]
    ] = {}
    ember = (255, 240, 210)
    flame = (255, 166, 58)
    soot = (64, 18, 8)
    ash = (24, 8, 6)
    for index, particle in enumerate(effect.particles):
        age = elapsed - particle.spawn_delay
        if age < 0.0 or age >= particle.lifetime:
            continue
        progress = age / max(0.001, particle.lifetime)
        flicker_step = int(age * particle.flicker_hz) + particle.phase
        flicker = hash_noise(index, flicker_step, effect_seed)
        if flicker % 13 == 0:
            continue
        wx = int(round(effect.x + particle.start_dx + particle.velocity_x * age))
        wy = int(round(effect.y + particle.start_dy + particle.velocity_y * age))
        shade_index = clamp(
            particle.shade + (1 if flicker % 6 == 0 else 0) - int(progress * 2.8),
            0,
            len(EXPLOSION_BLOCKS) - 1,
        )
        brightness = max(0.08, 1.0 - progress)
        fg_color = mix(
            soot, mix(flame, ember, 0.45 + brightness * 0.35), 0.35 + brightness * 0.65
        )
        bg_color = mix(ash, mix(soot, flame, 0.55), 0.18 + brightness * 0.58)
        strength = brightness + shade_index * 0.22 + (0.12 if flicker % 5 == 0 else 0.0)
        current = visuals.get((wx, wy))
        if current is not None and current[0] >= strength:
            continue
        visuals[(wx, wy)] = (
            strength,
            EXPLOSION_BLOCKS[shade_index],
            fg_color,
            bg_color,
        )
    return {
        (wx, wy): (ch, fg_color, bg_color)
        for (wx, wy), (_, ch, fg_color, bg_color) in visuals.items()
    }


def enemy_head_color(theme: Theme, enemy: Enemy) -> tuple[int, int, int]:
    if enemy.kind == "virus":
        return mix(theme["enemy"], theme["bytes"], 0.35)
    if enemy.kind == "blinder":
        return mix(theme["enemy"], theme["player"], 0.2)
    if enemy.kind == "fuse":
        return theme["enemy_alt"]
    return theme["enemy"]


def enemy_segment_color(theme: Theme, enemy: Enemy, index: int) -> tuple[int, int, int]:
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


class Renderer:
    def __init__(self) -> None:
        self.last_canvas: Canvas | None = None
        self.run_static_canvas: Canvas | None = None
        self.run_overlay_prev: dict[tuple[int, int], Cell] = {}
        self.run_camera_key: tuple[int, int, int, int, int, int] | None = None

    def reset_run_cache(self) -> None:
        self.run_static_canvas = None
        self.run_overlay_prev = {}
        self.run_camera_key = None

    def draw_panel(
        self,
        canvas: Canvas,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        title: str,
        theme: Theme,
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
        for corner in (
            (x, y),
            (x + width - 1, y),
            (x, y + height - 1),
            (x + width - 1, y + height - 1),
        ):
            canvas.put(
                corner[0], corner[1], "┼", fg_color=wall, bg_color=bg_color, bold=True
            )
        canvas.text(
            x + 2,
            y,
            f" {title} ",
            fg_color=theme["pickup"],
            bg_color=bg_color,
            bold=True,
        )

    def build_run_static_canvas(
        self, run: RunState, cols: int, rows: int, left: int, top: int
    ) -> Canvas:
        theme = run.sector.theme
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0,
            0,
            cols,
            rows,
            base=theme["bg"],
            alt=theme["bg_alt"],
            seed=run.sector.seed,
        )
        for sx in range(cols):
            wx = left + sx
            for sy in range(rows):
                wy = top + sy
                base = theme["floor"]
                if (wx, wy) in run.sector.walls:
                    glyph = "#" if (wx + wy) % 5 else "+"
                    canvas.put(sx, sy, glyph, fg_color=theme["wall"], bg_color=base)
                else:
                    noise = hash_noise(wx, wy, run.sector.seed)
                    if noise % 73 == 0:
                        dot_color = mix(theme["floor"], theme["muted"], 0.8)
                        canvas.put(sx, sy, ".", fg_color=dot_color, bg_color=base)
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
        now = time.monotonic()

        def on_screen(wx: int, wy: int) -> tuple[int, int] | None:
            sx = wx - left
            sy = wy - top
            if 0 <= sx < cols and 0 <= sy < rows:
                return sx, sy
            return None

        def put(
            x: int, y: int, ch: str, fg_color=None, bg_color=None, bold: bool = False
        ) -> None:
            if 0 <= x < cols and 0 <= y < rows and ch:
                overlay[(x, y)] = Cell(
                    ch[0],
                    fg_color,
                    bg_color if bg_color is not None else theme["floor"],
                    bold,
                )

        def text(
            x: int, y: int, value: str, fg_color=None, bg_color=None, bold: bool = False
        ) -> None:
            for idx, ch in enumerate(value):
                put(x + idx, y, ch, fg_color=fg_color, bg_color=bg_color, bold=bold)

        if run.ping_ticks > 0:
            for wx, wy in run.ping_path:
                if screen := on_screen(wx, wy):
                    put(
                        screen[0],
                        screen[1],
                        "·",
                        fg_color=theme["ping"],
                        bg_color=theme["floor"],
                    )

        ex, ey = run.sector.exit
        if screen := on_screen(ex, ey):
            pulse = 0.25 + 0.25 * (math.sin(time.monotonic() * 3.5) + 1) / 2
            exit_color = mix(theme["pickup"], theme["accent"], pulse)
            put(screen[0], screen[1], "x", fg_color=exit_color, bg_color=theme["floor"])

        for shard in run.sector.byte_shards:
            if screen := on_screen(shard.x, shard.y):
                put(
                    screen[0],
                    screen[1],
                    "$",
                    fg_color=theme["bytes"],
                    bg_color=theme["floor"],
                )

        for pickup in run.sector.pickups:
            if pickup.resolved:
                continue
            for i, ch in enumerate(pickup.text):
                if screen := on_screen(pickup.x + i, pickup.y):
                    put(
                        screen[0],
                        screen[1],
                        ch,
                        fg_color=pickup_letter_color(theme, pickup, i),
                        bg_color=theme["floor"],
                    )

        for mine in run.mines:
            if screen := on_screen(mine.x, mine.y):
                put(
                    screen[0],
                    screen[1],
                    "^",
                    fg_color=theme["accent"],
                    bg_color=theme["floor"],
                )

        for piece in run.wreckage:
            if screen := on_screen(piece.x, piece.y):
                put(
                    screen[0],
                    screen[1],
                    piece.ch or " ",
                    fg_color=debris_color(theme, piece, now=now),
                    bg_color=theme["floor"],
                )

        for enemy in active_enemies_for_run(run):
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
        for enemy in active_enemies_for_run(run):
            head_color = enemy_head_color(theme, enemy)
            if screen := on_screen(enemy.head.x, enemy.head.y):
                flash_fg, flash_bg = invert_colors(head_color, theme["floor"], blink)
                put(
                    screen[0],
                    screen[1],
                    enemy.head.ch,
                    fg_color=flash_fg,
                    bg_color=flash_bg,
                )

        for idx, segment in enumerate(run.body):
            if screen := on_screen(segment.x, segment.y):
                pickup_color = pickup_color_at_position(
                    theme, run, (segment.x, segment.y)
                )
                color = player_segment_color(
                    theme,
                    idx,
                    len(run.body),
                    infected=segment.infected > 0,
                    pickup_color=pickup_color,
                )
                put(
                    screen[0],
                    screen[1],
                    segment.ch or " ",
                    fg_color=color,
                    bg_color=theme["floor"],
                )

        for bomb in run.bombs:
            for wx, wy, ch in bomb_display_cells(bomb):
                if screen := on_screen(wx, wy):
                    put(
                        screen[0],
                        screen[1],
                        ch,
                        fg_color=bomb_text_color(theme),
                        bg_color=theme["floor"],
                        bold=True,
                    )

        if screen := on_screen(run.head.x, run.head.y):
            cursor_fg, cursor_bg = invert_colors(theme["player"], theme["floor"], blink)
            put(
                screen[0],
                screen[1],
                arrow_for_direction(run.direction),
                fg_color=cursor_fg,
                bg_color=cursor_bg,
            )

        for effect in active_explosions(run, now=now):
            for (wx, wy), (ch, fg_color, bg_color) in explosion_particle_visuals(
                effect, run.ticks, now=now
            ).items():
                if screen := on_screen(wx, wy):
                    put(
                        screen[0],
                        screen[1],
                        ch,
                        fg_color=fg_color,
                        bg_color=bg_color,
                        bold=True,
                    )

        if cols >= 56:
            bytes_text = f"b{run.bytes_collected}"
            text(
                max(1, cols - len(bytes_text) - 2),
                1,
                bytes_text,
                fg_color=theme["muted"],
            )
        if cols >= 72:
            active_cache = " ".join(
                f"{name}:{run.inventory.get(name, 0)}"
                for name in ABILITY_NAMES
                if run.inventory.get(name, 0)
            )
            if active_cache:
                text(
                    max(1, cols - len(active_cache) - 2),
                    rows - 2,
                    active_cache[: max(0, cols - 4)],
                    fg_color=theme["muted"],
                )
        if run.extracted:
            text(max(2, cols // 2 - 5), 2, "extracting", fg_color=theme["pickup"])
        elif run.game_over:
            text(max(2, cols // 2 - 5), 2, "terminated", fg_color=theme["enemy"])

        return overlay

    def compose_run_canvas(
        self, base_canvas: Canvas, overlay: dict[tuple[int, int], Cell]
    ) -> Canvas:
        full = Canvas(base_canvas.width, base_canvas.height, (0, 0, 0))
        full.cells = [
            [Cell(cell.ch, cell.fg, cell.bg, cell.bold) for cell in row]
            for row in base_canvas.cells
        ]
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
        if (
            target_canvas is None
            or target_canvas.width != base_canvas.width
            or target_canvas.height != base_canvas.height
        ):
            return self.compose_run_canvas(base_canvas, current)
        for x, y in set(previous) | set(current):
            cell = current.get((x, y))
            if cell is None:
                base_cell = base_canvas.cells[y][x]
                target_canvas.cells[y][x] = Cell(
                    base_cell.ch, base_cell.fg, base_cell.bg, base_cell.bold
                )
            else:
                target_canvas.cells[y][x] = Cell(cell.ch, cell.fg, cell.bg, cell.bold)
        return target_canvas

    def render_sparse_overlay_diff(
        self,
        base_canvas: Canvas,
        previous: dict[tuple[int, int], Cell],
        current: dict[tuple[int, int], Cell],
    ) -> str:
        positions = sorted(
            set(previous) | set(current), key=lambda pos: (pos[1], pos[0])
        )
        if not positions:
            return ""
        parts: list[str] = []
        current_style: (
            tuple[tuple[int, int, int] | None, tuple[int, int, int] | None, bool] | None
        ) = None
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

    def apply_blindness(self, canvas: Canvas, run: RunState) -> None:
        blind_tint = run.sector.theme["blind"]
        for row in range(canvas.height):
            for col in range(canvas.width):
                cell = canvas.cells[row][col]
                cell.bg = mix(cell.bg or blind_tint, blind_tint, 0.72)
                if hash_noise(col, row, run.ticks + run.blind_ticks) % 4 != 0:
                    cell.ch = " "
        canvas.text(
            2,
            2,
            "feed corrupted",
            fg_color=run.sector.theme["muted"],
            bg_color=blind_tint,
        )

    def render_menu(self, save: SaveData, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=2311
        )
        top = max(2, rows // 2 - 12)
        art_width = max(len(line) for line in RUN_ART)
        left = max(2, (cols - art_width) // 2)
        for i, line in enumerate(RUN_ART):
            canvas.text(
                left,
                top + i,
                line,
                fg_color=mix(
                    theme["accent"], theme["player"], i / max(1, len(RUN_ART) - 1)
                ),
                bold=True,
            )
        summary = "Type directly into the game, spell up/down/left/right to turn, use BACKSPACE to rewind. Typing it out is the only way."
        for idx, line in enumerate(wrap_lines(summary, max(30, cols - 12))):
            canvas.text(6, top + 8 + idx, line, fg_color=theme["muted"])
        options = [
            "[N] New Run",
            "[S] Shop",
            "[C] Credits",
            f"[A] Music: {'ON' if save.audio_enabled else 'OFF'}",
            f"[H] Hardcore: {'ON' if save.hardcore else 'OFF'}",
            "[Q] Quit",
        ]
        for idx, option in enumerate(options):
            highlight = idx in (3, 4)
            canvas.text(
                8,
                top + 12 + idx * 2,
                option,
                fg_color=theme["pickup"] if highlight else theme["player"],
                bold=True,
            )
        stats = [
            f"Banked bytes: {save.banked_bytes}",
            f"Win streak: {save.streak}",
            f"Upgrades: DASH {save.upgrades['dash_cache']} | PING {save.upgrades['ping_cache']} | MAGNET {save.upgrades['magnet']} | FOCUS {save.upgrades['focus']}",
        ]
        for idx, line in enumerate(stats):
            canvas.text(6, rows - 5 + idx, line[: cols - 10], fg_color=theme["accent"])
        return canvas

    def render_shop(
        self, save: SaveData, shop_cursor: int, cols: int, rows: int
    ) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=7001
        )
        panel_w = min(cols - 6, 90)
        panel_h = min(rows - 4, 20)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.draw_panel(
            canvas, x, y, panel_w, panel_h, title="BYTE MARKET", theme=theme
        )
        canvas.text(
            x + 3,
            y + 2,
            f"Banked bytes: {save.banked_bytes}",
            fg_color=theme["player"],
            bold=True,
        )
        canvas.text(
            x + 3, y + 3, "Press ENTER to buy. ESC to return.", fg_color=theme["muted"]
        )
        row_y = y + 5
        for idx, item in enumerate(SHOP_ITEMS):
            level = save.upgrades[item["id"]]
            cost = item["base_cost"] + level * 90
            selected = idx == shop_cursor
            tone = theme["pickup"] if selected else theme["player"]
            prefix = ">" if selected else " "
            canvas.text(
                x + 3,
                row_y,
                f"{prefix} {item['name']}  L{level}  COST {cost}",
                fg_color=tone,
                bold=selected,
            )
            for line_idx, line in enumerate(
                wrap_lines(item["description"], panel_w - 10)
            ):
                canvas.text(x + 6, row_y + 1 + line_idx, line, fg_color=theme["muted"])
            row_y += 4
        return canvas

    def render_result(
        self, run: RunState | None, save: SaveData, cols: int, rows: int
    ) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=415
        )
        panel_w = min(cols - 8, 84)
        panel_h = min(rows - 6, 18)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.draw_panel(canvas, x, y, panel_w, panel_h, title="RUN RESULT", theme=theme)
        if run is None:
            return canvas
        title = "EXTRACTION COMPLETE" if run.extracted else "SESSION TERMINATED"
        tone = theme["accent"] if run.extracted else theme["enemy"]
        canvas.text(x + 3, y + 2, title, fg_color=tone, bold=True)
        if run.extracted:
            reward = run.bytes_collected + 40 * run.kills
            lines = [
                f"Bytes banked: {reward}",
                f"Enemies deleted: {run.kills}",
                f"Streak: {save.streak}",
            ]
        else:
            lines = [
                f"Cause: {run.cause}",
                f"Bytes lost: {run.bytes_collected}",
                f"Streak reset to: {save.streak}",
            ]
        for idx, line in enumerate(lines):
            canvas.text(x + 3, y + 5 + idx * 2, line, fg_color=theme["player"])
        canvas.text(
            x + 3,
            y + panel_h - 3,
            "[ENTER] new run   [M] menu",
            fg_color=theme["pickup"],
            bold=True,
        )
        return canvas

    def render_credits(
        self, credits: CreditsState | None, cols: int, rows: int
    ) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=9113
        )
        max_text_width = max(20, cols - 6)
        text_block_width = min(
            max((len(line) for line, _, _ in CREDITS_PAGE_LINES), default=20),
            max_text_width,
        )
        text_x = max(2, (cols - text_block_width) // 2)
        text_y = 2
        line_y = text_y
        for line, color_key, bold in CREDITS_PAGE_LINES:
            for wrapped in wrap_lines(line, max_text_width):
                canvas.text(
                    text_x,
                    line_y,
                    wrapped,
                    fg_color=theme[color_key],
                    bold=bold,
                )
                line_y += 1
        if credits is not None:
            for idx, segment in enumerate(credits.body):
                draw_x = segment.x
                draw_y = segment.y
                color = player_segment_color(
                    theme, idx, len(credits.body), infected=False
                )
                canvas.put(
                    draw_x,
                    draw_y,
                    segment.ch or " ",
                    fg_color=color,
                    bg_color=theme["bg"],
                )
            blink = int(time.monotonic() * 2.6) % 2 == 0
            cursor_fg, cursor_bg = invert_colors(theme["player"], theme["bg"], blink)
            canvas.put(
                credits.head.x,
                credits.head.y,
                arrow_for_direction(credits.direction),
                fg_color=cursor_fg,
                bg_color=cursor_bg,
            )
        return canvas

    def render_run(self, run: RunState, cols: int, rows: int) -> Canvas:
        theme = run.sector.theme
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0,
            0,
            cols,
            rows,
            base=theme["bg"],
            alt=theme["bg_alt"],
            seed=run.sector.seed,
        )
        if rows < 14 or cols < 48:
            canvas.text(
                2,
                2,
                "Window too small. Expand the terminal to continue the run.",
                fg_color=theme["pickup"],
            )
            return canvas

        left, top = update_camera(run, cols, rows)
        ensure_generated_rect(run.sector, left, top, cols, rows)
        base_canvas = self.build_run_static_canvas(run, cols, rows, left, top)
        overlay = self.build_run_overlay(run, cols, rows, left, top)
        canvas = self.compose_run_canvas(base_canvas, overlay)
        if run.blind_ticks > 0:
            self.apply_blindness(canvas, run)
        return canvas

    def render_canvas(
        self,
        state: str,
        run: RunState | None,
        credits: CreditsState | None,
        save: SaveData,
        shop_cursor: int,
        cols: int,
        rows: int,
    ) -> Canvas:
        if state == "menu":
            return self.render_menu(save, cols, rows)
        if state == "shop":
            return self.render_shop(save, shop_cursor, cols, rows)
        if state == "credits":
            return self.render_credits(credits, cols, rows)
        if state == "result":
            return self.render_result(run, save, cols, rows)
        if run is None:
            return self.render_menu(save, cols, rows)
        return self.render_run(run, cols, rows)

    def render_frame(
        self,
        state: str,
        run: RunState | None,
        credits: CreditsState | None,
        save: SaveData,
        shop_cursor: int,
        cols: int,
        rows: int,
    ) -> str:
        optimized_run_frame = (
            state == "run"
            and run is not None
            and run.blind_ticks <= 0
            and rows >= 14
            and cols >= 48
        )
        if optimized_run_frame:
            left, top = update_camera(run, cols, rows)
            ensure_generated_rect(run.sector, left, top, cols, rows)
            camera_key = (
                cols,
                rows,
                left,
                top,
                run.sector.seed,
                run.sector.terrain_revision,
            )
            if self.run_static_canvas is None or self.run_camera_key != camera_key:
                self.run_camera_key = camera_key
                self.run_static_canvas = self.build_run_static_canvas(
                    run, cols, rows, left, top
                )
                self.run_overlay_prev = self.build_run_overlay(
                    run, cols, rows, left, top
                )
                full = self.compose_run_canvas(
                    self.run_static_canvas, self.run_overlay_prev
                )
                frame = full.render_diff(self.last_canvas)
                self.last_canvas = full
                return frame

            overlay = self.build_run_overlay(run, cols, rows, left, top)
            frame = self.render_sparse_overlay_diff(
                self.run_static_canvas, self.run_overlay_prev, overlay
            )
            self.last_canvas = self.apply_overlay_to_canvas(
                self.run_static_canvas,
                self.last_canvas,
                self.run_overlay_prev,
                overlay,
            )
            self.run_overlay_prev = overlay
            return frame

        self.reset_run_cache()
        canvas = self.render_canvas(state, run, credits, save, shop_cursor, cols, rows)
        frame = canvas.render_diff(self.last_canvas)
        self.last_canvas = canvas
        return frame
