from __future__ import annotations

import random
from collections import deque

from .constants import ABILITY_NAMES, CHUNK_SIZE, DIRECTIONS, GENERATION_RADIUS, SECTOR_NAMES, THEMES
from .models import ByteShard, Enemy, Pickup, SaveData, Sector, Segment
from .utils import hash_noise, manhattan, random_word


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

    enemy_total = 0 if near_start else (1 if rng.random() < 0.2 else 0)
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
