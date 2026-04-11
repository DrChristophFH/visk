from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .constants import DIRECTIONS, MAX_ENEMY_LENGTH
from .generation import ensure_generated_around
from .models import Enemy, RunState, SaveData, Segment
from .utils import manhattan

if TYPE_CHECKING:
    from .world_controller import WorldController


ENEMY_GLYPHS = "!$%&*+?{}[]/\\<>="


def active_enemy_limit(run: RunState) -> int:
    return 2 + run.ticks // 90


def active_enemies_for_run(run: RunState) -> list[Enemy]:
    living = [enemy for enemy in run.sector.enemies if not enemy.dead]
    return living[: active_enemy_limit(run)]


class EnemyBehavior:
    def navigation_target(self, run: RunState, enemy: Enemy) -> tuple[int, int]:
        return run.head.x, run.head.y

    def after_update(self, actor: "EnemyActor") -> None:
        return


class ChaserBehavior(EnemyBehavior):
    pass


class VirusBehavior(EnemyBehavior):
    def navigation_target(self, run: RunState, enemy: Enemy) -> tuple[int, int]:
        target_cells = [segment for segment in run.body if segment.infected <= 0]
        if not target_cells:
            return run.head.x, run.head.y
        target_segment = min(
            target_cells,
            key=lambda segment: manhattan(
                (segment.x, segment.y),
                (enemy.head.x, enemy.head.y),
            ),
        )
        return target_segment.x, target_segment.y

    def after_update(self, actor: "EnemyActor") -> None:
        head = actor.head_position()
        nearest = min(
            actor.run.body,
            key=lambda segment: manhattan((segment.x, segment.y), head),
        )
        if manhattan((nearest.x, nearest.y), head) > 1:
            return
        nearest.infected = max(nearest.infected, 6)
        stolen = min(14, actor.run.bytes_collected)
        if stolen:
            actor.run.bytes_collected -= stolen
            actor.run.log(f"VIRUS siphoned {stolen} bytes.")


class BlinderBehavior(EnemyBehavior):
    def after_update(self, actor: "EnemyActor") -> None:
        if manhattan(actor.head_position(), (actor.run.head.x, actor.run.head.y)) > 3:
            return
        duration = max(4, 12 - actor.save.upgrades["focus"] * 3)
        actor.run.blind_ticks = max(actor.run.blind_ticks, duration)
        actor.run.log("BLINDER corrupted the feed.")


class FuseBehavior(EnemyBehavior):
    def after_update(self, actor: "EnemyActor") -> None:
        enemy = actor.enemy
        if enemy.fuse_timer > 0:
            enemy.fuse_timer -= 1
            if enemy.fuse_timer == 0:
                actor.apply_explosion(
                    actor.run,
                    enemy.head.x,
                    enemy.head.y,
                    2,
                    "fuse",
                )
                actor.kill_enemy(actor.run, enemy, "chain")
                actor.world.invalidate_nav_cache()
                actor.run.log("FUSE detonated.")
            return
        if any(
            manhattan((segment.x, segment.y), actor.head_position()) <= 2
            for segment in actor.run.body
        ):
            enemy.fuse_timer = 2
            actor.run.log("FUSE armed.")


BEHAVIORS: dict[str, EnemyBehavior] = {
    "chaser": ChaserBehavior(),
    "virus": VirusBehavior(),
    "blinder": BlinderBehavior(),
    "fuse": FuseBehavior(),
}


def behavior_for_enemy(enemy: Enemy) -> EnemyBehavior:
    return BEHAVIORS.get(enemy.kind, BEHAVIORS["chaser"])


@dataclass
class EnemyActor:
    run: RunState
    save: SaveData
    enemy: Enemy
    world: "WorldController"
    rng: random.Random
    occupied: set[tuple[int, int]]
    grow: bool
    trim_player_history: Callable[[RunState, int, str], None]
    apply_explosion: Callable[[RunState, int, int, int, str], None]
    kill_enemy: Callable[[RunState, Enemy, str], None]

    @property
    def behavior(self) -> EnemyBehavior:
        return behavior_for_enemy(self.enemy)

    def head_position(self) -> tuple[int, int]:
        return self.enemy.head.x, self.enemy.head.y

    def update(self) -> None:
        if self.enemy.dead:
            return
        for segment in self.enemy.body:
            self.occupied.discard((segment.x, segment.y))
        previous_head = self.head_position()
        next_pos = self._select_move()
        if next_pos == previous_head:
            self.apply_explosion(
                self.run,
                self.enemy.head.x,
                self.enemy.head.y,
                1,
                "stuck",
            )
            self.world.invalidate_nav_cache()
            self.run.log(f"{self.enemy.kind.upper()} locked up and exploded.")
            for piece in self.run.wreckage:
                self.occupied.add((piece.x, piece.y))
            return
        self.enemy.heading = (
            next_pos[0] - self.enemy.head.x,
            next_pos[1] - self.enemy.head.y,
        )
        self.enemy.body.append(
            Segment(next_pos[0], next_pos[1], self.rng.choice(ENEMY_GLYPHS))
        )
        should_grow = self.grow and len(self.enemy.body) <= MAX_ENEMY_LENGTH
        if not should_grow:
            self.enemy.body.popleft()
        for segment in self.enemy.body:
            self.occupied.add((segment.x, segment.y))
        keep_updating = self._resolve_common_effects()
        if not keep_updating:
            return
        self.behavior.after_update(self)

    def _select_move(self) -> tuple[int, int]:
        current = self.head_position()
        candidates = self._candidate_moves(current)
        if not candidates:
            self.enemy.last_move_mode = "blocked"
            return current
        target = (self.run.head.x, self.run.head.y)
        safe_moves = [
            move
            for move in candidates
            if self._future_move_is_safe(move, target, lookahead_steps=4)
        ]
        considered = safe_moves or candidates
        best_move = min(
            considered,
            key=lambda move: self._move_priority(current, move, target),
        )
        self.enemy.last_move_mode = "follow" if safe_moves else "forced"
        return best_move

    def _move_priority(
        self,
        current: tuple[int, int],
        move: tuple[int, int],
        target: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        heading = (move[0] - current[0], move[1] - current[1])
        turn_penalty = 0 if heading == self.enemy.heading else 1
        return (manhattan(move, target), turn_penalty, move[1], move[0])

    def _future_move_is_safe(
        self,
        first_move: tuple[int, int],
        target: tuple[int, int],
        *,
        lookahead_steps: int,
    ) -> bool:
        simulated_body = deque((segment.x, segment.y) for segment in self.enemy.body)
        current = self.head_position()
        heading = self.enemy.heading

        for step in range(lookahead_steps):
            next_pos = (
                first_move
                if step == 0
                else self._future_best_move(current, simulated_body, target, heading)
            )
            if next_pos is None:
                return False
            heading = (next_pos[0] - current[0], next_pos[1] - current[1])
            should_grow = self.grow and len(simulated_body) <= MAX_ENEMY_LENGTH
            simulated_body.append(next_pos)
            if not should_grow:
                simulated_body.popleft()
            current = next_pos

        return True

    def _future_best_move(
        self,
        current: tuple[int, int],
        simulated_body: deque[tuple[int, int]],
        target: tuple[int, int],
        heading: tuple[int, int],
    ) -> tuple[int, int] | None:
        candidates = self._future_candidate_moves(current, simulated_body)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda move: (
                manhattan(move, target),
                0 if (move[0] - current[0], move[1] - current[1]) == heading else 1,
                move[1],
                move[0],
            ),
        )

    def _future_candidate_moves(
        self,
        current: tuple[int, int],
        simulated_body: deque[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        own_cells = set(simulated_body)
        candidates: list[tuple[int, int]] = []
        for dx, dy in DIRECTIONS.values():
            nxt = current[0] + dx, current[1] + dy
            if not self.world.is_generated_cell(*nxt):
                continue
            if nxt in self.run.sector.walls or nxt in own_cells:
                continue
            candidates.append(nxt)
        return candidates

    def _candidate_moves(
        self, current: tuple[int, int]
    ) -> list[tuple[int, int]]:
        own_cells = {(segment.x, segment.y) for segment in self.enemy.body}
        candidates: list[tuple[int, int]] = []
        for dx, dy in DIRECTIONS.values():
            nxt = current[0] + dx, current[1] + dy
            if not self.world.is_generated_cell(*nxt):
                continue
            if nxt in self.occupied or nxt in own_cells:
                continue
            candidates.append(nxt)
        return candidates

    def _resolve_common_effects(self) -> bool:
        head = self.head_position()
        trail_hits = [
            index
            for index, segment in enumerate(self.run.body)
            if (segment.x, segment.y) == head
        ]
        if trail_hits and trail_hits[-1] == len(self.run.body) - 1:
            self.run.game_over = True
            self.run.cause = f"{self.enemy.kind} reached your cursor"
            return False
        if trail_hits:
            self.trim_player_history(self.run, trail_hits[-1] + 1, self.enemy.kind)
            self.world.invalidate_nav_cache()
            return False
        return True


def update_enemies(
    run: RunState,
    save: SaveData,
    world: "WorldController",
    rng: random.Random,
    *,
    grow: bool,
    body_positions: Callable[[object], set[tuple[int, int]]],
    wreckage_positions: Callable[[RunState], set[tuple[int, int]]],
    trim_player_history: Callable[[RunState, int, str], None],
    apply_explosion: Callable[[RunState, int, int, int, str], None],
    kill_enemy: Callable[[RunState, Enemy, str], None],
) -> None:
    if run.silence_ticks > 0:
        run.silence_ticks -= 1
        return

    ensure_generated_around(run.sector, (run.head.x, run.head.y))
    active_enemies = active_enemies_for_run(run)
    occupied = run.sector.walls | body_positions(run.body) | wreckage_positions(run)
    for enemy in active_enemies:
        if enemy.dead:
            continue
        for segment in enemy.body:
            occupied.add((segment.x, segment.y))

    for enemy in active_enemies:
        if enemy.dead:
            continue
        actor = EnemyActor(
            run=run,
            save=save,
            enemy=enemy,
            world=world,
            rng=rng,
            occupied=occupied,
            grow=grow,
            trim_player_history=trim_player_history,
            apply_explosion=apply_explosion,
            kill_enemy=kill_enemy,
        )
        actor.update()
        if run.game_over:
            return
