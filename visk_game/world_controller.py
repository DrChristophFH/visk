from __future__ import annotations

from collections.abc import Callable

from .constants import DIRECTIONS
from .generation import chunk_coords, ensure_generated_around
from .models import Bomb, Enemy, Mine, PingTrace, RunState
from .utils import manhattan

GridPos = tuple[int, int]


class WorldController:
    def __init__(
        self,
        run: RunState,
        *,
        active_enemies_for_run: Callable[[RunState], list[Enemy]],
        kill_enemy: Callable[[RunState, Enemy, str], None],
    ) -> None:
        self.run = run
        self._active_enemies_for_run = active_enemies_for_run
        self._kill_enemy = kill_enemy
        self._body_positions_cache: set[GridPos] | None = None
        self._wreckage_positions_cache: set[GridPos] | None = None
        self._enemy_positions_cache: set[GridPos] | None = None

    def closest_enemy(self) -> Enemy | None:
        living = self._active_enemies_for_run(self.run)
        if not living:
            return None
        return min(
            living,
            key=lambda enemy: manhattan(
                (enemy.head.x, enemy.head.y), (self.run.head.x, self.run.head.y)
            ),
        )

    def kill_enemy(self, enemy: Enemy, reason: str) -> None:
        self._kill_enemy(self.run, enemy, reason)

    def next_object_id(self, prefix: str) -> str:
        self.run.next_object_id += 1
        return f"{prefix}:{self.run.next_object_id}"

    def remove_object(self, object_id: str) -> bool:
        for attr in ("bombs", "mines", "pings"):
            collection = getattr(self.run, attr)
            for item in list(collection):
                if item.object_id == object_id:
                    collection.remove(item)
                    return True
        return False

    def spawn_bomb_from_player(self, player) -> str:
        recent_segments = list(self.run.body)[-4:]
        bomb_cells = (
            tuple((segment.x, segment.y) for segment in recent_segments)
            if len(recent_segments) == 4
            else ()
        )
        center = bomb_cells[1] if len(bomb_cells) == 4 else player.head_position()
        bomb = Bomb(
            object_id=self.next_object_id("bomb"),
            x=center[0],
            y=center[1],
            fuse=6,
            radius=2,
            cells=bomb_cells,
        )
        self.run.bombs.append(bomb)
        return bomb.object_id

    def spawn_mine_at_player(self, player) -> str:
        dx, dy = DIRECTIONS[player.direction()]
        anchor_x = player.head_position()[0] - dx * 2
        anchor_y = player.head_position()[1] - dy * 2
        mine = Mine(
            object_id=self.next_object_id("mine"),
            x=anchor_x,
            y=anchor_y,
        )
        self.run.mines.append(mine)
        return mine.object_id

    def ping_exit_target(self) -> tuple[int, int] | None:
        return self.run.sector.exit[0] + 3, self.run.sector.exit[1]

    def trace_line(
        self, start: tuple[int, int], target: tuple[int, int], max_tiles: int
    ) -> list[tuple[int, int]]:
        x0, y0 = start
        x1, y1 = target
        if start == target or max_tiles <= 0:
            return []

        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        cells: list[tuple[int, int]] = []

        while (x, y) != (x1, y1) and len(cells) < max_tiles:
            e2 = err * 2
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
            cells.append((x, y))
        return cells

    def spawn_ping_trace(self, path: list[tuple[int, int]], duration: int) -> str:
        ping = PingTrace(
            object_id=self.next_object_id("ping"),
            path=path,
            ticks_remaining=duration,
        )
        self.run.pings.append(ping)
        return ping.object_id

    def set_silence_ticks(self, ticks: int) -> None:
        self.run.silence_ticks = max(self.run.silence_ticks, ticks)

    def find_dash_target(self, direction: str, steps: int) -> tuple[int, int] | None:
        dx, dy = DIRECTIONS[direction]
        target = (self.run.head.x + dx * steps, self.run.head.y + dy * steps)
        ensure_generated_around(self.run.sector, target, 1)
        if target in self.run.sector.walls:
            return None
        if target in self._body_positions():
            return None
        if target in self._wreckage_positions():
            return None
        if target in self._enemy_positions(self._active_enemies_for_run(self.run)):
            return None
        return target

    def invalidate_nav_cache(self) -> None:
        return

    def is_generated_cell(self, x: int, y: int) -> bool:
        return (
            chunk_coords(x, y, self.run.sector.chunk_size)
            in self.run.sector.generated_chunks
        )

    def _body_positions(self) -> set[GridPos]:
        if self._body_positions_cache is None:
            self._body_positions_cache = {
                (segment.x, segment.y) for segment in self.run.body
            }
        return self._body_positions_cache

    def _wreckage_positions(self) -> set[GridPos]:
        if self._wreckage_positions_cache is None:
            self._wreckage_positions_cache = {
                (piece.x, piece.y) for piece in self.run.wreckage
            }
        return self._wreckage_positions_cache

    def _enemy_positions(self, enemies: list[Enemy]) -> set[GridPos]:
        if self._enemy_positions_cache is None:
            cells: set[GridPos] = set()
            for enemy in enemies:
                if enemy.dead:
                    continue
                for segment in enemy.body:
                    cells.add((segment.x, segment.y))
            self._enemy_positions_cache = cells
        return self._enemy_positions_cache
