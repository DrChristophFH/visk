from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .constants import DIRECTIONS
from .generation import chunk_coords, ensure_generated_around
from .models import Bomb, Enemy, Mine, PingTrace, RunState
from .utils import manhattan

GridPos = tuple[int, int]
DistanceField = dict[GridPos, int]


@dataclass
class Nav:
    world: "WorldController"
    target: GridPos
    distance_map: DistanceField

    def get_distance(self, x: int, y: int) -> int | None:
        if (x, y) == self.target:
            return 0
        return self.distance_map.get((x, y))

    def get_all_moves_from(self, x: int, y: int) -> list[GridPos]:
        moves: list[tuple[int, GridPos]] = []
        for dx, dy in DIRECTIONS.values():
            nxt = (x + dx, y + dy)
            distance = self.get_distance(*nxt)
            if distance is None:
                continue
            moves.append((distance, nxt))
        moves.sort(key=lambda item: item[0])
        return [move for _, move in moves]

    def get_best_move_from(self, x: int, y: int) -> GridPos | None:
        moves = self.get_all_moves_from(x, y)
        return moves[0] if moves else None

    def get_path_from(self, x: int, y: int, max_steps: int | None = None) -> list[GridPos]:
        if (x, y) == self.target:
            return []
        if self.get_distance(x, y) is None:
            return []
        path: list[GridPos] = []
        current = (x, y)
        remaining = max_steps
        while current != self.target and (remaining is None or remaining > 0):
            nxt = self.get_best_move_from(*current)
            if nxt is None or nxt == current:
                break
            path.append(nxt)
            current = nxt
            if remaining is not None:
                remaining -= 1
        return path


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
        self._nav_cache: dict[GridPos, Nav] = {}

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

    def find_dash_target(
        self, direction: str, steps: int
    ) -> tuple[int, int] | None:
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

    def get_nav_for_target(self, target_x: int, target_y: int) -> Nav:
        target = (target_x, target_y)
        cached = self._nav_cache.get(target)
        if cached is not None:
            return cached
        nav = Nav(
            world=self,
            target=target,
            distance_map=self._build_distance_field((target,)),
        )
        self._nav_cache[target] = nav
        return nav

    def invalidate_nav_cache(self) -> None:
        self._nav_cache.clear()

    def _build_distance_field(self, targets: Iterable[GridPos]) -> DistanceField:
        blocked = self.run.sector.walls | self._body_positions() | self._wreckage_positions()
        queue: deque[GridPos] = deque()
        distances: DistanceField = {}
        for source in self._navigation_sources(targets, blocked):
            if source in distances:
                continue
            distances[source] = 0
            queue.append(source)

        while queue:
            current = queue.popleft()
            next_distance = distances[current] + 1
            for dx, dy in DIRECTIONS.values():
                nxt = current[0] + dx, current[1] + dy
                if nxt in distances or nxt in blocked or not self._is_generated(nxt):
                    continue
                distances[nxt] = next_distance
                queue.append(nxt)
        return distances

    def _navigation_sources(
        self,
        targets: Iterable[GridPos],
        blocked: set[GridPos],
    ) -> set[GridPos]:
        sources: set[GridPos] = set()
        for target in targets:
            if not self._is_generated(target):
                continue
            if target not in blocked:
                sources.add(target)
                continue
            for dx, dy in DIRECTIONS.values():
                neighbor = (target[0] + dx, target[1] + dy)
                if neighbor in blocked or not self._is_generated(neighbor):
                    continue
                sources.add(neighbor)
        return sources

    def is_generated_cell(self, x: int, y: int) -> bool:
        return chunk_coords(x, y, self.run.sector.chunk_size) in self.run.sector.generated_chunks

    def _is_generated(self, cell: GridPos) -> bool:
        return self.is_generated_cell(cell[0], cell[1])

    def _body_positions(self) -> set[GridPos]:
        return {(segment.x, segment.y) for segment in self.run.body}

    def _wreckage_positions(self) -> set[GridPos]:
        return {(piece.x, piece.y) for piece in self.run.wreckage}

    def _enemy_positions(self, enemies: list[Enemy]) -> set[GridPos]:
        cells: set[GridPos] = set()
        for enemy in enemies:
            if enemy.dead:
                continue
            for segment in enemy.body:
                cells.add((segment.x, segment.y))
        return cells
