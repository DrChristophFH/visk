from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import UndoAction


class PlayerLike(Protocol):
    def head_position(self) -> tuple[int, int]:
        ...

    def direction(self) -> str:
        ...

    def inventory_available(self, name: str) -> bool:
        ...

    def consume_inventory(self, name: str) -> bool:
        ...

    def refund_inventory(self, name: str) -> None:
        ...

    def queue_dash(self, target: tuple[int, int]) -> None:
        ...

    def attach_undo_action(self, action: UndoAction) -> None:
        ...

    def log(self, message: str) -> None:
        ...


class WorldLike(Protocol):
    def closest_enemy(self):
        ...

    def kill_enemy(self, enemy, reason: str) -> None:
        ...

    def find_dash_target(self, direction: str, steps: int) -> tuple[int, int] | None:
        ...

    def spawn_bomb_from_player(self, player: PlayerLike) -> str:
        ...

    def spawn_mine_at_player(self, player: PlayerLike) -> str:
        ...

    def ping_exit_target(self) -> tuple[int, int] | None:
        ...

    def trace_line(
        self, start: tuple[int, int], target: tuple[int, int], max_tiles: int
    ) -> list[tuple[int, int]]:
        ...

    def spawn_ping_trace(self, path: list[tuple[int, int]], duration: int) -> str:
        ...

    def set_silence_ticks(self, ticks: int) -> None:
        ...


@dataclass
class AbilityContext:
    player: PlayerLike
    world: WorldLike
    typed_char: str
    command: str


class Ability(Protocol):
    name: str
    commands: tuple[str, ...]

    def execute(self, ctx: AbilityContext) -> None:
        ...
