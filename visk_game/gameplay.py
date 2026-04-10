from __future__ import annotations

import random
import time
from collections import deque
from typing import Callable

from .constants import ABILITY_NAMES, DIRECTIONS, EXIT_TEXT, MAX_ENEMY_LENGTH
from .generation import chunk_coords, ensure_generated_around, exit_cells, generate_sector
from .models import Bomb, CommandUndo, Debris, Enemy, ExplosionEffect, ExplosionParticle, ExtractAttempt, Mine, PickupAttempt, RunState, SaveData, Sector, Segment
from .utils import manhattan

PING_DRAW_DISTANCE = 12
PING_DURATION_TICKS = 18


def ping_exit_target(run: RunState) -> tuple[int, int]:
    return run.sector.exit[0] + 3, run.sector.exit[1]


PING_TARGETS: dict[str, tuple[str, Callable[[RunState], tuple[int, int] | None]]] = {
    "ping_exit": ("extraction", ping_exit_target),
}
INLINE_ABILITY_NAMES = tuple(name for name in ABILITY_NAMES if name != "ping")


def active_enemy_limit(run: RunState) -> int:
    return 2 + run.ticks // 90


def active_enemies_for_run(run: RunState) -> list[Enemy]:
    living = [enemy for enemy in run.sector.enemies if not enemy.dead]
    return living[: active_enemy_limit(run)]


def wreckage_positions(run: RunState) -> set[tuple[int, int]]:
    return {(piece.x, piece.y) for piece in run.wreckage}


def add_wreckage(run: RunState, segments: list[Segment], origin: str) -> None:
    occupied = wreckage_positions(run)
    now = time.monotonic()
    for segment in segments:
        pos = (segment.x, segment.y)
        if pos in occupied:
            continue
        run.wreckage.append(Debris(segment.x, segment.y, segment.ch or " ", origin, created_at=now))
        occupied.add(pos)


def show_run_help(run: RunState) -> None:
    run.log("Action keys are ticks. Type direction words inline to turn, abilities inline to execute, ping targets like PING_EXIT to scan, BACKSPACE to rewind; SPACE does nothing.")


def show_run_status(run: RunState) -> None:
    stocked = " ".join(f"{name}:{run.inventory.get(name, 0)}" for name in ABILITY_NAMES if run.inventory.get(name, 0))
    run.log(f"Status dir={run.direction.upper()} bytes={run.bytes_collected} inv={stocked or 'none'}.")


def create_run(save: SaveData) -> RunState:
    sector, start = generate_sector(save)
    body = deque([Segment(start[0], start[1], " ")])
    inventory = {name: 999 for name in ABILITY_NAMES}
    inventory["dash"] += save.upgrades["dash_cache"]
    inventory["ping"] += save.upgrades["ping_cache"]
    run = RunState(sector=sector, body=body, direction="right", inventory=inventory, hardcore=save.hardcore)
    run.log(f"Link established. Sector {sector.name}.")
    show_run_help(run)
    return run


def body_positions(body: deque[Segment]) -> set[tuple[int, int]]:
    return {(segment.x, segment.y) for segment in body}


def add_typed_bytes(run: RunState, amount: int) -> None:
    if amount > 0:
        run.bytes_collected += amount


def remove_typed_bytes(run: RunState, amount: int) -> None:
    if amount > 0:
        run.bytes_collected = max(0, run.bytes_collected - amount)


def explosion_cells(x: int, y: int, radius: int) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) <= radius:
                cells.add((x + dx, y + dy))
    return cells


def disrupt_pickup_attempt(run: RunState, reason: str) -> None:
    if run.pickup_attempt is None:
        return
    pickup = run.sector.pickups[run.pickup_attempt.pickup_index]
    fail_pickup(run, pickup)
    run.pickup_attempt = None
    run.log(f"{pickup.text.upper()} lost on {reason}.")


def trim_player_history(run: RunState, retained_start: int, reason: str) -> None:
    if retained_start <= 0:
        return
    body_segments = list(run.body)
    removed_segments = body_segments[:retained_start]
    kept_segments = body_segments[retained_start:]
    if not kept_segments:
        kept_segments = [run.head]
    if removed_segments:
        add_wreckage(run, removed_segments, "player")
    run.body = deque(kept_segments)
    run.pending_command = run.pending_command[-max(0, len(run.body) - 1) :]
    adjusted_undos: list[tuple[int, str, str]] = []
    for step_index, previous_direction, previous_pending in run.direction_undos:
        new_step = step_index - retained_start
        if new_step > 0:
            adjusted_undos.append((new_step, previous_direction, previous_pending[-max(0, new_step) :]))
    run.direction_undos = adjusted_undos
    adjusted_command_undos: list[CommandUndo] = []
    for undo in run.command_undos:
        new_step = undo.step_index - retained_start
        if new_step > 0:
            adjusted_command_undos.append(
                CommandUndo(
                    step_index=new_step,
                    previous_pending=undo.previous_pending[-max(0, new_step) :],
                    refund_ability=undo.refund_ability,
                    bomb=undo.bomb,
                    mine=undo.mine,
                )
            )
    run.command_undos = adjusted_command_undos
    disrupt_pickup_attempt(run, reason)
    reset_extract_attempt(run)
    run.log(f"Trail cut by {reason.upper()}.")


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


def active_explosions(run: RunState, *, now: float | None = None) -> list[ExplosionEffect]:
    now = time.monotonic() if now is None else now
    active = [effect for effect in run.explosions if now - effect.started_at < effect.duration]
    run.explosions = active
    return active


def closest_enemy(run: RunState) -> Enemy | None:
    living = active_enemies_for_run(run)
    if not living:
        return None
    return min(living, key=lambda enemy: manhattan((enemy.head.x, enemy.head.y), (run.head.x, run.head.y)))


def kill_enemy(run: RunState, enemy: Enemy, reason: str) -> None:
    kill_enemy_with_wreckage(run, enemy, reason, leave_wreckage=True)


def kill_enemy_with_wreckage(run: RunState, enemy: Enemy, reason: str, *, leave_wreckage: bool) -> None:
    if enemy.dead:
        return
    enemy.dead = True
    if leave_wreckage:
        add_wreckage(run, list(enemy.body), enemy.kind)
    run.kills += 1
    run.bytes_collected += 40
    run.log(f"{enemy.kind.upper()} deleted via {reason}. +40 bytes.")


def create_explosion_effect(x: int, y: int, radius: int, *, started_at: float) -> ExplosionEffect:
    rng = random.Random(time.monotonic_ns() ^ (x << 20) ^ (y << 8) ^ (radius << 2))
    impact_cells = list(explosion_cells(x, y, radius))
    particle_count = 36 + radius * 54
    particles: list[ExplosionParticle] = []
    max_end_time = 0.0
    for _ in range(particle_count):
        cell_x, cell_y = rng.choice(impact_cells)
        cell_dx = cell_x - x
        cell_dy = cell_y - y
        direction_x = cell_dx + rng.uniform(-0.85, 0.85)
        direction_y = cell_dy + rng.uniform(-0.85, 0.85)
        if abs(direction_x) + abs(direction_y) < 0.2:
            direction_x = rng.uniform(-1.0, 1.0)
            direction_y = rng.uniform(-1.0, 1.0)
        scale = max(0.35, abs(direction_x) + abs(direction_y))
        speed = rng.uniform(0.85, 3.2) + radius * 0.45
        spawn_delay = rng.uniform(0.0, 0.12 + radius * 0.03)
        lifetime = rng.uniform(0.14, 0.38 + radius * 0.18)
        max_end_time = max(max_end_time, spawn_delay + lifetime)
        particles.append(
            ExplosionParticle(
                start_dx=cell_dx * rng.uniform(0.1, 0.95) + rng.uniform(-0.46, 0.46),
                start_dy=cell_dy * rng.uniform(0.1, 0.95) + rng.uniform(-0.46, 0.46),
                velocity_x=direction_x / scale * speed,
                velocity_y=direction_y / scale * speed,
                spawn_delay=spawn_delay,
                lifetime=lifetime,
                flicker_hz=rng.uniform(16.0, 40.0),
                phase=rng.randrange(1 << 16),
                shade=rng.randrange(4),
            )
        )
    return ExplosionEffect(
        x=x,
        y=y,
        radius=radius,
        started_at=started_at,
        duration=max(0.42, max_end_time + 0.08),
        particles=particles,
    )


def remove_player_segments_in_zone(run: RunState, impact_zone: set[tuple[int, int]], reason: str) -> None:
    if (run.head.x, run.head.y) in impact_zone:
        run.game_over = True
        run.cause = "exploded"
        return
    hit_indices = [index for index, segment in enumerate(run.body) if (segment.x, segment.y) in impact_zone]
    if not hit_indices:
        return
    remove_typed_bytes(run, len(hit_indices))
    trim_player_history(run, hit_indices[-1] + 1, reason)
    run.wreckage = [piece for piece in run.wreckage if (piece.x, piece.y) not in impact_zone]


def remove_enemy_segments_in_zone(run: RunState, enemy: Enemy, impact_zone: set[tuple[int, int]], reason: str) -> None:
    if enemy.dead:
        return
    if not any((segment.x, segment.y) in impact_zone for segment in enemy.body):
        return
    remaining_segments = [segment for segment in enemy.body if (segment.x, segment.y) not in impact_zone]
    if remaining_segments:
        add_wreckage(run, remaining_segments, enemy.kind)
    kill_enemy_with_wreckage(run, enemy, reason, leave_wreckage=False)


def apply_explosion(run: RunState, x: int, y: int, radius: int, owner: str) -> None:
    started_at = time.monotonic()
    impact_zone = explosion_cells(x, y, radius)
    run.explosions.append(create_explosion_effect(x, y, radius, started_at=started_at))
    removed_walls = run.sector.walls & impact_zone
    if removed_walls:
        run.sector.walls.difference_update(removed_walls)
        run.sector.terrain_revision += 1
    if run.wreckage:
        run.wreckage = [piece for piece in run.wreckage if (piece.x, piece.y) not in impact_zone]
    remove_failed_pickups_in_zone(run, impact_zone)
    remove_player_segments_in_zone(run, impact_zone, owner)
    for enemy in run.sector.enemies:
        remove_enemy_segments_in_zone(run, enemy, impact_zone, owner.upper())


def update_hazards(run: RunState, *, advance_bombs: bool) -> None:
    remaining_bombs: list[Bomb] = []
    for bomb in run.bombs:
        if advance_bombs:
            bomb.fuse -= 1
        if bomb.fuse < 0:
            apply_explosion(run, bomb.x, bomb.y, bomb.radius, bomb.owner)
            run.log(f"{bomb.owner.upper()} bomb detonated.")
        else:
            remaining_bombs.append(bomb)
    run.bombs = remaining_bombs

    remaining_mines: list[Mine] = []
    for mine in run.mines:
        triggered = False
        for enemy in active_enemies_for_run(run):
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


def dash_head_target(run: RunState, steps: int) -> tuple[int, int] | None:
    dx, dy = step_from_direction(run.direction)
    target = (run.head.x + dx * steps, run.head.y + dy * steps)
    ensure_generated_around(run.sector, target, 1)
    if target in run.sector.walls:
        return None
    if target in body_positions(run.body):
        return None
    if target in wreckage_positions(run):
        return None
    if target in enemy_positions(active_enemies_for_run(run)):
        return None
    return target


def on_exit(sector: Sector, position: tuple[int, int]) -> bool:
    return position in set(exit_cells(sector.exit))


def exit_index(sector: Sector, position: tuple[int, int]) -> int | None:
    for index, cell in enumerate(exit_cells(sector.exit)):
        if cell == position:
            return index
    return None


def fail_pickup(run: RunState, pickup) -> None:
    if pickup.failed or pickup.resolved:
        return
    pickup.failed = True
    pickup.matched_indices.clear()
    pickup.error_indices.clear()
    run.sector.terrain_revision += 1


def remove_failed_pickups_in_zone(run: RunState, impact_zone: set[tuple[int, int]]) -> None:
    removed_any = False
    for pickup in run.sector.pickups:
        if pickup.resolved or not pickup.failed:
            continue
        if any(cell in impact_zone for cell in pickup.cells()):
            pickup.resolved = True
            removed_any = True
    if removed_any:
        run.sector.terrain_revision += 1


def reset_extract_attempt(run: RunState) -> None:
    run.extract_attempt = None
    run.extract_matched_indices.clear()


def ping_line(start: tuple[int, int], target: tuple[int, int], max_tiles: int) -> list[tuple[int, int]]:
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


def use_ping_command(run: RunState, command: str) -> bool:
    target_spec = PING_TARGETS.get(command)
    if target_spec is None:
        return False
    if run.inventory.get("ping", 0) <= 0:
        run.log("PING unavailable.")
        return True

    label, resolver = target_spec
    target = resolver(run)
    if target is None:
        run.log(f"{command.upper()} found no target.")
        return True

    run.inventory["ping"] -= 1
    run.ping_path = ping_line((run.head.x, run.head.y), target, PING_DRAW_DISTANCE)
    run.ping_ticks = PING_DURATION_TICKS
    run.log(f"{command.upper()} traced {label}.")
    return True


def use_ability(run: RunState, name: str) -> Bomb | Mine | tuple[int, int] | None:
    if run.inventory.get(name, 0) <= 0:
        run.log(f"{name.upper()} unavailable.")
        return None
    run.inventory[name] -= 1
    if name == "zap":
        enemy = closest_enemy(run)
        if enemy is None:
            run.log("ZAP found no target.")
            run.inventory[name] += 1
            return None
        kill_enemy(run, enemy, "zap")
    elif name == "bomb":
        recent_segments = list(run.body)[-4:]
        bomb_cells = tuple((segment.x, segment.y) for segment in recent_segments) if len(recent_segments) == 4 else ()
        center = bomb_cells[1] if len(bomb_cells) == 4 else (run.head.x, run.head.y)
        bomb = Bomb(center[0], center[1], fuse=6, radius=2, cells=bomb_cells)
        run.bombs.append(bomb)
        run.log("BOMB armed. Fuse: 5.")
        return bomb
    elif name == "mine":
        mine = Mine(run.head.x, run.head.y)
        run.mines.append(mine)
        run.log("MINE deployed.")
        return mine
    elif name == "silence":
        run.silence_ticks = 20
        run.log("SILENCE injected. Enemies paused for 20 ticks.")
    elif name == "dash":
        target = dash_head_target(run, 4)
        if target is None:
            run.log("DASH obstructed.")
            run.inventory[name] += 1
            return None
        run.log("DASH executed.")
        return target
    return None


def append_command_undo(
    run: RunState,
    *,
    step_index: int,
    previous_pending: str,
    refund_ability: str | None = None,
    bomb: Bomb | None = None,
    mine: Mine | None = None,
) -> None:
    run.command_undos.append(
        CommandUndo(
            step_index=step_index,
            previous_pending=previous_pending,
            refund_ability=refund_ability,
            bomb=bomb,
            mine=mine,
        )
    )


def undo_command_effect(run: RunState, undo: CommandUndo) -> None:
    refunded = False
    if undo.refund_ability == "bomb" and undo.bomb in run.bombs:
        run.bombs.remove(undo.bomb)
        run.inventory["bomb"] = run.inventory.get("bomb", 0) + 1
        refunded = True
    elif undo.refund_ability == "mine" and undo.mine in run.mines:
        run.mines.remove(undo.mine)
        run.inventory["mine"] = run.inventory.get("mine", 0) + 1
        refunded = True
    if refunded:
        run.log(f"{undo.refund_ability.upper()} rewound.")


def maybe_collect_byte(run: RunState, position: tuple[int, int] | None = None) -> None:
    position = position or (run.head.x, run.head.y)
    for shard in list(run.sector.byte_shards):
        if (shard.x, shard.y) == position:
            run.bytes_collected += shard.value
            run.sector.byte_shards.remove(shard)
            run.log(f"Byte shard extracted. +{shard.value}.")
            return


def begin_or_update_extract(run: RunState, typed_char: str, position: tuple[int, int]) -> bool:
    current_index = exit_index(run.sector, position)
    if run.extract_attempt is not None:
        expected_index = (
            len(EXIT_TEXT) - 1 - run.extract_attempt.progress
            if run.extract_attempt.reverse
            else run.extract_attempt.progress
        )
        if current_index != expected_index:
            reset_extract_attempt(run)
            if current_index is None:
                return False
    if current_index is None:
        return False

    reverse = run.direction == "left"
    if run.extract_attempt is None:
        valid_start = (not reverse and current_index == 0) or (reverse and current_index == len(EXIT_TEXT) - 1)
        if not valid_start:
            reset_extract_attempt(run)
            return True
        expected = EXIT_TEXT[current_index]
        if typed_char.lower() != expected:
            reset_extract_attempt(run)
            run.log("EXTRACT rejected.")
            return True
        run.extract_matched_indices.add(current_index)
        run.extract_attempt = ExtractAttempt(reverse=reverse, progress=1)
        if len(EXIT_TEXT) == 1:
            run.extracted = True
            run.game_over = True
            run.cause = "extracted"
        return True

    expected = EXIT_TEXT[current_index]
    if typed_char.lower() != expected:
        reset_extract_attempt(run)
        run.log("EXTRACT rejected.")
        return True
    run.extract_matched_indices.add(current_index)
    run.extract_attempt.progress += 1
    if run.extract_attempt.progress >= len(EXIT_TEXT):
        run.extracted = True
        run.game_over = True
        run.cause = "extracted"
        run.log("EXTRACT accepted.")
        return True
    return True


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
            fail_pickup(run, pickup)
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
            fail_pickup(run, pickup)
            run.log(f"{pickup.text.upper()} corrupted.")
            return True
        expected = pickup.text[current_index]
        if typed_char.lower() != expected:
            fail_pickup(run, pickup)
            run.log(f"{pickup.text.upper()} mistyped and purged.")
            return True
        pickup.matched_indices.add(current_index)
        run.pickup_attempt = PickupAttempt(current_pickup_index, reverse, 1)
        if len(pickup.text) == 1:
            pickup.resolved = True
            run.inventory[pickup.ability] += 1
            run.log(f"{pickup.ability.upper()} acquired.")
            run.pickup_attempt = None
        return True

    expected = pickup.text[current_index]
    if typed_char.lower() != expected:
        fail_pickup(run, pickup)
        run.pickup_attempt = None
        run.log(f"{pickup.text.upper()} mistyped and purged.")
        return True
    pickup.matched_indices.add(current_index)
    run.pickup_attempt.progress += 1
    if run.pickup_attempt.progress >= len(pickup.text):
        pickup.resolved = True
        run.inventory[pickup.ability] += 1
        run.log(f"{pickup.ability.upper()} acquired.")
        run.pickup_attempt = None
    return True


def resolve_inline_command(run: RunState) -> tuple[int, int] | None:
    if not run.pending_command:
        return None
    suffix = run.pending_command.lower()
    commands = sorted(("status", "help", *PING_TARGETS.keys(), *INLINE_ABILITY_NAMES, *DIRECTIONS.keys()), key=len, reverse=True)
    for command in commands:
        if not suffix.endswith(command):
            continue
        previous_pending = run.pending_command[:-1]
        step_index = len(run.body)
        movement_override: tuple[int, int] | None = None
        if command in DIRECTIONS:
            run.direction_undos.append((step_index, run.direction, previous_pending))
            run.direction = command
        elif command in PING_TARGETS:
            use_ping_command(run, command)
            append_command_undo(run, step_index=step_index, previous_pending=previous_pending)
        elif command in ABILITY_NAMES:
            effect = use_ability(run, command)
            if command == "bomb":
                append_command_undo(
                    run,
                    step_index=step_index,
                    previous_pending=previous_pending,
                    refund_ability="bomb" if isinstance(effect, Bomb) else None,
                    bomb=effect if isinstance(effect, Bomb) else None,
                )
            elif command == "mine":
                append_command_undo(
                    run,
                    step_index=step_index,
                    previous_pending=previous_pending,
                    refund_ability="mine" if isinstance(effect, Mine) else None,
                    mine=effect if isinstance(effect, Mine) else None,
                )
            elif command == "dash":
                append_command_undo(run, step_index=step_index, previous_pending=previous_pending)
                if isinstance(effect, tuple):
                    movement_override = effect
            else:
                append_command_undo(run, step_index=step_index, previous_pending=previous_pending)
        elif command == "help":
            show_run_help(run)
            append_command_undo(run, step_index=step_index, previous_pending=previous_pending)
        else:
            show_run_status(run)
            append_command_undo(run, step_index=step_index, previous_pending=previous_pending)
        run.pending_command = ""
        return movement_override
    return None


def advance_player(run: RunState, typed_char: str) -> None:
    advance_player_with_mode(run, typed_char, record_command=True)


def advance_player_with_mode(run: RunState, typed_char: str, *, record_command: bool) -> None:
    current_head = run.head
    dx, dy = step_from_direction(run.direction)
    nx = current_head.x + dx
    ny = current_head.y + dy
    next_pos = (nx, ny)
    ensure_generated_around(run.sector, next_pos, 1)

    def collision_cause(position: tuple[int, int]) -> str | None:
        if position in run.sector.walls:
            return "wall collision"
        if position in {(segment.x, segment.y) for segment in list(run.body)[:-1]}:
            return "self collision"
        if position in wreckage_positions(run):
            return "wreckage collision"
        if position in enemy_positions(active_enemies_for_run(run)):
            return "enemy collision"
        return None

    dash_candidate = record_command and (run.pending_command + typed_char).lower().endswith("dash")
    if not dash_candidate:
        cause = collision_cause(next_pos)
        if cause is not None:
            run.game_over = True
            run.cause = cause
            return

    current_position = (current_head.x, current_head.y)
    movement_target = next_pos
    dash_jump_start: tuple[int, int] | None = None
    dash_v_position: tuple[int, int] | None = None
    if record_command:
        run.pending_command += typed_char
        extract_touched = begin_or_update_extract(run, typed_char, current_position)
        if run.game_over:
            return
        pickup_touched = False if extract_touched else begin_or_update_pickup(run, typed_char, current_position)
        if not extract_touched and not pickup_touched:
            dash_target = resolve_inline_command(run)
            if dash_target is not None:
                movement_target = dash_target
                dash_jump_start = next_pos
                dash_v_position = (movement_target[0] - dx, movement_target[1] - dy)

    cause = collision_cause(movement_target)
    if cause is not None:
        run.game_over = True
        run.cause = cause
        return

    current_head.ch = typed_char
    add_typed_bytes(run, 1)
    maybe_collect_byte(run, current_position)
    if dash_jump_start is not None:
        run.body.append(Segment(dash_jump_start[0], dash_jump_start[1], "^"))
        if dash_v_position != dash_jump_start:
            run.body.append(Segment(dash_v_position[0], dash_v_position[1], "v"))
        run.body.append(Segment(movement_target[0], movement_target[1], " "))
        maybe_collect_byte(run, movement_target)
    else:
        run.body.append(Segment(movement_target[0], movement_target[1], " "))


def retract_player(run: RunState) -> None:
    if len(run.body) <= 1:
        return
    if manhattan((run.body[-1].x, run.body[-1].y), (run.body[-2].x, run.body[-2].y)) > 1:
        return
    current_step = len(run.body) - 1
    if run.pending_command:
        run.pending_command = run.pending_command[:-1]
    elif run.command_undos and run.command_undos[-1].step_index == current_step:
        undo = run.command_undos.pop()
        undo_command_effect(run, undo)
        run.pending_command = undo.previous_pending
    elif run.direction_undos and run.direction_undos[-1][0] == current_step:
        _, previous_direction, previous_pending = run.direction_undos.pop()
        run.direction = previous_direction
        run.pending_command = previous_pending
    elif run.body[-1].ch == " " and run.body[-2].ch == "v":
        run.body.pop()
        run.body[-1].ch = " "
        disrupt_pickup_attempt(run, "rollback")
        reset_extract_attempt(run)
        return
    else:
        return
    remove_typed_bytes(run, 1)
    run.body.pop()
    run.body[-1].ch = " "
    disrupt_pickup_attempt(run, "rollback")
    reset_extract_attempt(run)


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
    own_cells = {(segment.x, segment.y) for segment in enemy.body}
    candidates: list[tuple[int, int]] = []
    for direction in DIRECTIONS.values():
        nxt = enemy.head.x + direction[0], enemy.head.y + direction[1]
        if chunk_coords(nxt[0], nxt[1], run.sector.chunk_size) not in run.sector.generated_chunks:
            continue
        if nxt in run.sector.walls or nxt in occupied or nxt in own_cells:
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
    trail_hits = [index for index, segment in enumerate(run.body) if (segment.x, segment.y) == head]
    if trail_hits and trail_hits[-1] == len(run.body) - 1:
        run.game_over = True
        run.cause = f"{enemy.kind} reached your cursor"
        return
    if trail_hits:
        trim_player_history(run, trail_hits[-1] + 1, enemy.kind)
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
        for segment in enemy.body:
            occupied.discard((segment.x, segment.y))
        previous_head = (enemy.head.x, enemy.head.y)
        next_pos = resolve_enemy_step(run, enemy, rng, occupied)
        if next_pos == previous_head:
            apply_explosion(run, enemy.head.x, enemy.head.y, 1, "stuck")
            run.log(f"{enemy.kind.upper()} locked up and exploded.")
            for piece in run.wreckage:
                occupied.add((piece.x, piece.y))
            if run.game_over:
                return
            continue
        enemy.heading = (next_pos[0] - enemy.head.x, next_pos[1] - enemy.head.y)
        enemy.body.append(Segment(next_pos[0], next_pos[1], random.choice("!$%&*+?{}[]/\\<>=")))
        should_grow = grow and len(enemy.body) <= MAX_ENEMY_LENGTH
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
    active_explosions(run)
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
    update_hazards(run, advance_bombs=True)
    if run.game_over:
        return
    advance_enemies(run, save, rng, grow=reason == "key")
    update_hazards(run, advance_bombs=False)
    decay_effects(run)
    run.ticks += 1
    if reason == "idle" and not run.game_over:
        run.log("Hardcore clock ticked.")
