from __future__ import annotations

import random
import time
from collections import deque
from typing import Callable

from .constants import ABILITY_NAMES, DIRECTIONS, MAX_ENEMY_LENGTH
from .generation import bfs_world, chunk_coords, ensure_generated_around, ensure_generated_rect, generate_sector
from .models import Bomb, Debris, Enemy, ExplosionEffect, ExplosionParticle, Mine, PickupAttempt, RunState, SaveData, Sector, Segment
from .utils import manhattan


def active_enemy_limit(run: RunState) -> int:
    return 2 + run.ticks // 90


def active_enemies_for_run(run: RunState) -> list[Enemy]:
    living = [enemy for enemy in run.sector.enemies if not enemy.dead]
    return living[: active_enemy_limit(run)]


def wreckage_positions(run: RunState) -> set[tuple[int, int]]:
    return {(piece.x, piece.y) for piece in run.wreckage}


def add_wreckage(run: RunState, segments: list[Segment], origin: str) -> None:
    occupied = wreckage_positions(run)
    for segment in segments:
        pos = (segment.x, segment.y)
        if pos in occupied:
            continue
        run.wreckage.append(Debris(segment.x, segment.y, segment.ch or " ", origin))
        occupied.add(pos)


def show_run_help(run: RunState) -> None:
    run.log("Action keys are ticks. Type direction words inline to turn, ability names inline to execute, BACKSPACE to rewind; SPACE does nothing.")


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
    pickup.failed = True
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
    disrupt_pickup_attempt(run, reason)
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
    trim_player_history(run, hit_indices[-1] + 1, reason)
    run.wreckage = [piece for piece in run.wreckage if (piece.x, piece.y) not in impact_zone]


def remove_enemy_segments_in_zone(run: RunState, enemy: Enemy, impact_zone: set[tuple[int, int]], reason: str) -> None:
    if enemy.dead:
        return
    survivors = deque(segment for segment in enemy.body if (segment.x, segment.y) not in impact_zone)
    if len(survivors) == len(enemy.body):
        return
    if not survivors:
        kill_enemy_with_wreckage(run, enemy, reason, leave_wreckage=False)
        return
    enemy.body = survivors


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
        recent_segments = list(run.body)[-4:]
        bomb_cells = tuple((segment.x, segment.y) for segment in recent_segments) if len(recent_segments) == 4 else ()
        center = bomb_cells[1] if len(bomb_cells) == 4 else (run.head.x, run.head.y)
        run.bombs.append(Bomb(center[0], center[1], fuse=6, radius=2, cells=bomb_cells))
        run.log("BOMB armed. Fuse: 5.")
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
        blocked = enemy_positions(active_enemies_for_run(run)) | body_positions(run.body) | wreckage_positions(run)
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
        blockers = body_positions(run.body) | enemy_positions(active_enemies_for_run(run)) | wreckage_positions(run)
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
            if current_pickup_index == run.pickup_attempt.pickup_index and current_index is not None:
                pickup.error_indices.add(current_index)
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
            pickup.error_indices.add(current_index)
            run.log(f"{pickup.text.upper()} corrupted.")
            return True
        expected = pickup.text[current_index]
        if typed_char.lower() != expected:
            pickup.failed = True
            pickup.error_indices.add(current_index)
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
        pickup.failed = True
        pickup.error_indices.add(current_index)
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


def resolve_inline_command(run: RunState) -> None:
    if not run.pending_command:
        return
    suffix = run.pending_command.lower()
    commands = sorted(("status", "help", *ABILITY_NAMES, *DIRECTIONS.keys()), key=len, reverse=True)
    for command in commands:
        if not suffix.endswith(command):
            continue
        if command in DIRECTIONS:
            run.direction_undos.append((len(run.body), run.direction, run.pending_command[:-1]))
            run.direction = command
        elif command in ABILITY_NAMES:
            use_ability(run, command)
        elif command == "help":
            show_run_help(run)
        else:
            show_run_status(run)
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
    if next_pos in wreckage_positions(run):
        run.game_over = True
        run.cause = "wreckage collision"
        return
    if next_pos in enemy_positions(active_enemies_for_run(run)):
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
    disrupt_pickup_attempt(run, "rollback")


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
