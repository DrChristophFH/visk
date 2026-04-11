from __future__ import annotations

import math
import time

from ..constants import ABILITY_NAMES, EXIT_TEXT, THEMES
from ..enemies import active_enemies_for_run
from ..gameplay import active_explosions
from ..generation import ensure_generated_rect
from ..models import Canvas, Debris, Enemy, ExplosionEffect, Pickup, RunState, Theme
from ..scene_types import SceneLayer, SceneRenderResult
from ..utils import Colors, arrow_for_direction, clamp, hash_noise, invert_colors, mix
from .base import BaseScene
from .shared import player_segment_color


class ChunkSpatialIndex:
    def __init__(self, chunk_size: int) -> None:
        self.chunk_size = chunk_size
        self.buckets: dict[tuple[int, int], set[tuple[int, int]]] = {}

    def clear(self) -> None:
        self.buckets.clear()

    def add_chunk_points(
        self, chunk: tuple[int, int], points: set[tuple[int, int]]
    ) -> None:
        if points:
            self.buckets[chunk] = set(points)

    def add_point(self, point: tuple[int, int]) -> None:
        chunk = point[0] // self.chunk_size, point[1] // self.chunk_size
        bucket = self.buckets.setdefault(chunk, set())
        bucket.add(point)

    def remove_point(self, point: tuple[int, int]) -> None:
        chunk = point[0] // self.chunk_size, point[1] // self.chunk_size
        bucket = self.buckets.get(chunk)
        if bucket is None:
            return
        bucket.discard(point)
        if not bucket:
            self.buckets.pop(chunk, None)

    def iter_rect(self, left: int, top: int, right: int, bottom: int):
        start_cx = left // self.chunk_size
        end_cx = (right - 1) // self.chunk_size
        start_cy = top // self.chunk_size
        end_cy = (bottom - 1) // self.chunk_size
        for cy in range(start_cy, end_cy + 1):
            for cx in range(start_cx, end_cx + 1):
                for x, y in self.buckets.get((cx, cy), ()):
                    if left <= x < right and top <= y < bottom:
                        yield x, y


def debris_color(theme: Theme, piece: Debris, *, now: float) -> tuple[int, int, int]:
    if piece.origin == "player":
        base_color = mix(theme["player"], theme["muted"], 0.55)
    else:
        base_color = mix(theme["enemy"], theme["wall"], 0.45)
    age = max(0.0, now - piece.created_at)
    fade = max(0.0, min(1.0, age / max(0.001, piece.fade_duration)))
    return mix(base_color, theme["wall"], fade)


def pickup_letter_color(theme: Theme, pickup: Pickup, index: int) -> tuple[int, int, int]:
    if index in pickup.error_indices:
        return theme["enemy"]
    if index in pickup.matched_indices:
        return mix(theme["bytes"], theme["player"], 0.3)
    if pickup.failed:
        return mix(theme["enemy"], theme["muted"], 0.35)
    return theme["pickup"]


def exit_letter_color(theme: Theme, run: RunState, index: int) -> tuple[int, int, int]:
    if index in run.extract_matched_indices:
        return mix(theme["bytes"], theme["player"], 0.3)
    return theme["pickup"]


def failed_pickup_color(theme: Theme) -> tuple[int, int, int]:
    return mix(theme["wall"], theme["muted"], 0.45)


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


class RunScene(BaseScene):
    name = "run"

    def __init__(self, renderer, session) -> None:
        super().__init__(renderer, session)
        self.overlay_canvas: Canvas | None = None
        self.static_canvas: Canvas | None = None
        self.static_index_sector_id: int | None = None
        self.static_index_seed: int | None = None
        self.static_index_chunk_size: int | None = None
        self.static_index_terrain_revision: int | None = None
        self.indexed_dot_chunks: set[tuple[int, int]] = set()
        self.indexed_wall_chunks: set[tuple[int, int]] = set()
        self.wall_snapshot: set[tuple[int, int]] = set()
        self.dot_index = ChunkSpatialIndex(1)
        self.wall_index = ChunkSpatialIndex(1)

    def reset_static_indexes(self, run: RunState) -> None:
        chunk_size = run.sector.chunk_size
        self.static_index_sector_id = id(run.sector)
        self.static_index_seed = run.sector.seed
        self.static_index_chunk_size = chunk_size
        self.static_index_terrain_revision = run.sector.terrain_revision
        self.indexed_dot_chunks.clear()
        self.indexed_wall_chunks.clear()
        self.wall_snapshot = set(run.sector.walls)
        self.dot_index = ChunkSpatialIndex(chunk_size)
        self.wall_index = ChunkSpatialIndex(chunk_size)

    def index_generated_chunk(self, run: RunState, chunk: tuple[int, int]) -> None:
        if chunk in self.indexed_wall_chunks and chunk in self.indexed_dot_chunks:
            return
        chunk_size = run.sector.chunk_size
        base_x = chunk[0] * chunk_size
        base_y = chunk[1] * chunk_size
        if chunk not in self.indexed_wall_chunks:
            chunk_walls = {
                (x, y)
                for x in range(base_x, base_x + chunk_size)
                for y in range(base_y, base_y + chunk_size)
                if (x, y) in run.sector.walls
            }
            self.wall_index.add_chunk_points(chunk, chunk_walls)
            self.indexed_wall_chunks.add(chunk)
        if chunk not in self.indexed_dot_chunks:
            chunk_dots = {
                (x, y)
                for x in range(base_x, base_x + chunk_size)
                for y in range(base_y, base_y + chunk_size)
                if (x, y) not in run.sector.walls
                and hash_noise(x, y, run.sector.seed) % 73 == 0
            }
            self.dot_index.add_chunk_points(chunk, chunk_dots)
            self.indexed_dot_chunks.add(chunk)

    def ensure_static_indexes(self, run: RunState) -> None:
        if (
            self.static_index_sector_id != id(run.sector)
            or
            self.static_index_seed != run.sector.seed
            or self.static_index_chunk_size != run.sector.chunk_size
        ):
            self.reset_static_indexes(run)
        for chunk in run.sector.generated_chunks:
            self.index_generated_chunk(run, chunk)
        if self.static_index_terrain_revision == run.sector.terrain_revision:
            return
        current_walls = run.sector.walls
        removed = self.wall_snapshot - current_walls
        added = current_walls - self.wall_snapshot
        for wall in removed:
            self.wall_index.remove_point(wall)
        for wall in added:
            self.wall_index.add_point(wall)
        self.wall_snapshot = set(current_walls)
        self.static_index_terrain_revision = run.sector.terrain_revision

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        run = self.session.run
        if run is None:
            return SceneRenderResult(
                [
                    SceneLayer(
                        key="run.fallback",
                        z_index=0,
                        cache_key=("run.fallback", cols, rows),
                        build_canvas=lambda cols=cols, rows=rows: self.build_fallback_canvas(
                            cols, rows
                        ),
                    )
                ]
            )
        if rows < 14 or cols < 48 or run.blind_ticks > 0:
            return SceneRenderResult(
                [
                    SceneLayer(
                        key="run.full",
                        z_index=0,
                        cache_key=None,
                        build_canvas=lambda cols=cols, rows=rows: self.build_full_canvas(
                            cols, rows
                        ),
                    )
                ]
            )

        left, top = update_camera(run, cols, rows)
        ensure_generated_rect(run.sector, left, top, cols, rows)
        static_key = (
            "run.static",
            cols,
            rows,
            left,
            top,
            run.sector.seed,
            run.sector.terrain_revision,
        )
        layers = [
            SceneLayer(
                key="run.static",
                z_index=0,
                cache_key=static_key,
                build_canvas=lambda run=run, cols=cols, rows=rows, left=left, top=top: self.build_static_canvas(
                    run, cols, rows, left, top
                ),
            ),
            SceneLayer(
                key="run.overlay",
                z_index=1,
                cache_key=None,
                build_canvas=lambda run=run, cols=cols, rows=rows, left=left, top=top: self.build_overlay_canvas(
                    run, cols, rows, left, top
                ),
            ),
        ]
        return SceneRenderResult(layers)

    def build_fallback_canvas(self, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=123
        )
        canvas.text(2, 2, "No active run.", fg_color=theme["pickup"])
        return canvas

    def build_full_canvas(self, cols: int, rows: int) -> Canvas:
        run = self.session.run
        if run is None:
            return self.build_fallback_canvas(cols, rows)
        theme = run.sector.theme
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=run.sector.seed
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
        static_canvas = self.build_static_canvas(run, cols, rows, left, top)
        overlay_canvas = self.build_overlay_canvas(run, cols, rows, left, top)
        canvas = static_canvas.copy()
        self.renderer.apply_layer(canvas, overlay_canvas)
        if run.blind_ticks > 0:
            self.apply_blindness(canvas, run)
        return canvas

    def build_static_canvas(
        self, run: RunState, cols: int, rows: int, left: int, top: int
    ) -> Canvas:
        theme = run.sector.theme
        floor = theme["floor"]
        self.ensure_static_indexes(run)
        if (
            self.static_canvas is None
            or self.static_canvas.width != cols
            or self.static_canvas.height != rows
        ):
            self.static_canvas = Canvas(cols, rows, floor)
        else:
            self.static_canvas.clear(background=floor)
        canvas = self.static_canvas
        wall = theme["wall"]
        dot_color = Colors.mix(floor, theme["muted"], 0.8)
        failed_pickup = failed_pickup_color(theme)
        right = left + cols
        bottom = top + rows
        for wx, wy in self.wall_index.iter_rect(left, top, right, bottom):
            glyph = "#" if (wx + wy) % 5 else "+"
            canvas.put(wx - left, wy - top, glyph, fg_color=wall, bg_color=floor)
        for wx, wy in self.dot_index.iter_rect(left, top, right, bottom):
            canvas.put(wx - left, wy - top, ".", fg_color=dot_color, bg_color=floor)
        for pickup in run.sector.pickups:
            if pickup.resolved or not pickup.failed:
                continue
            for index, ch in enumerate(pickup.text):
                wx = pickup.x + index
                wy = pickup.y
                sx = wx - left
                sy = wy - top
                if 0 <= sx < cols and 0 <= sy < rows:
                    canvas.put(
                        sx,
                        sy,
                        ch,
                        fg_color=failed_pickup,
                        bg_color=floor,
                    )
        return canvas

    def build_overlay_canvas(
        self, run: RunState, cols: int, rows: int, left: int, top: int
    ) -> Canvas:
        theme = run.sector.theme
        if (
            self.overlay_canvas is None
            or self.overlay_canvas.width != cols
            or self.overlay_canvas.height != rows
        ):
            self.overlay_canvas = Canvas.transparent(cols, rows)
        else:
            self.overlay_canvas.clear()
        canvas = self.overlay_canvas
        now = time.monotonic()
        floor = theme["floor"]
        right = left + cols
        bottom = top + rows

        def on_screen(wx: int, wy: int) -> tuple[int, int] | None:
            if left <= wx < right and top <= wy < bottom:
                return wx - left, wy - top
            return None

        def put(x: int, y: int, ch: str, fg_color=None, bg_color=None, bold: bool = False) -> None:
            if 0 <= x < cols and 0 <= y < rows and ch:
                canvas.put(
                    x,
                    y,
                    ch,
                    fg_color=fg_color,
                    bg_color=bg_color if bg_color is not None else floor,
                    bold=bold,
                )

        def text(x: int, y: int, value: str, fg_color=None, bg_color=None, bold: bool = False) -> None:
            for idx, ch in enumerate(value):
                put(x + idx, y, ch, fg_color=fg_color, bg_color=bg_color, bold=bold)

        for ping in run.pings:
            ping.render(on_screen, put, theme)

        ex, ey = run.sector.exit
        pulse = 0.25 + 0.25 * (math.sin(now * 3.5) + 1) / 2
        for index, ch in enumerate(EXIT_TEXT):
            if screen := on_screen(ex + index, ey):
                put(
                    screen[0],
                    screen[1],
                    ch,
                    fg_color=mix(exit_letter_color(theme, run, index), theme["accent"], pulse),
                )

        for shard in run.sector.byte_shards:
            if screen := on_screen(shard.x, shard.y):
                put(screen[0], screen[1], "$", fg_color=theme["bytes"])

        for pickup in run.sector.pickups:
            if pickup.resolved or pickup.failed:
                continue
            for i, ch in enumerate(pickup.text):
                if screen := on_screen(pickup.x + i, pickup.y):
                    put(screen[0], screen[1], ch, fg_color=pickup_letter_color(theme, pickup, i))

        pickup_colors = {
            (pickup.x + index, pickup.y): pickup_letter_color(theme, pickup, index)
            for pickup in run.sector.pickups
            if not pickup.resolved and not pickup.failed
            for index in (pickup.matched_indices | pickup.error_indices)
        }

        for piece in run.wreckage:
            if screen := on_screen(piece.x, piece.y):
                put(screen[0], screen[1], piece.ch or " ", fg_color=debris_color(theme, piece, now=now))

        active_enemies = active_enemies_for_run(run)
        for enemy in active_enemies:
            for idx, segment in enumerate(enemy.body):
                if screen := on_screen(segment.x, segment.y):
                    put(screen[0], screen[1], segment.ch, fg_color=enemy_segment_color(theme, enemy, idx))

        blink = int(now * 2.6) % 2 == 0
        for enemy in active_enemies:
            head_color = enemy_head_color(theme, enemy)
            if screen := on_screen(enemy.head.x, enemy.head.y):
                flash_fg, flash_bg = invert_colors(head_color, floor, blink)
                put(screen[0], screen[1], enemy.head.ch, fg_color=flash_fg, bg_color=flash_bg)

        for idx, segment in enumerate(run.body):
            if screen := on_screen(segment.x, segment.y):
                pickup_color = pickup_colors.get((segment.x, segment.y))
                color = player_segment_color(
                    theme,
                    idx,
                    len(run.body),
                    infected=segment.infected > 0,
                    pickup_color=pickup_color,
                )
                put(screen[0], screen[1], segment.ch or " ", fg_color=color)

        for mine in run.mines:
            mine.render(
                on_screen,
                put,
                theme,
                blink=blink,
                invert_colors=invert_colors,
            )

        for bomb in run.bombs:
            bomb.render(on_screen, put, theme)

        if screen := on_screen(run.head.x, run.head.y):
            cursor_fg, cursor_bg = invert_colors(theme["player"], floor, blink)
            cursor_glyph = run.head.ch if run.head.ch in "^v" else arrow_for_direction(run.direction)
            put(screen[0], screen[1], cursor_glyph, fg_color=cursor_fg, bg_color=cursor_bg)

        for effect in active_explosions(run, now=now):
            for (wx, wy), (ch, fg_color, bg_color) in explosion_particle_visuals(
                effect, run.ticks, now=now
            ).items():
                if screen := on_screen(wx, wy):
                    put(screen[0], screen[1], ch, fg_color=fg_color, bg_color=bg_color, bold=True)

        if cols >= 56:
            bytes_text = f"bytes:{run.bytes_collected}".ljust(12)
            text(max(1, cols - len(bytes_text) - 2), 1, bytes_text, fg_color=theme["bytes"])
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

        return canvas

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
