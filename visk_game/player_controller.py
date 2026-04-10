from __future__ import annotations

from collections.abc import Callable

from .models import RunState, UndoAction


class PlayerController:
    def __init__(
        self,
        run: RunState,
        *,
        typed_char: str,
        current_position: tuple[int, int],
        next_position: tuple[int, int],
        step_index: int,
        append_command_undo: Callable[..., None],
    ) -> None:
        self.run = run
        self.typed_char = typed_char
        self.current_position = current_position
        self.next_position = next_position
        self.step_index = step_index
        self.movement_target = next_position
        self.dash_jump_start: tuple[int, int] | None = None
        self.dash_v_position: tuple[int, int] | None = None
        self._undo_action: UndoAction | None = None
        self._append_command_undo = append_command_undo

    def head_position(self) -> tuple[int, int]:
        return self.current_position

    def direction(self) -> str:
        return self.run.direction

    def inventory_available(self, name: str) -> bool:
        return self.run.inventory.get(name, 0) > 0

    def consume_inventory(self, name: str) -> bool:
        if not self.inventory_available(name):
            return False
        self.run.inventory[name] -= 1
        return True

    def refund_inventory(self, name: str) -> None:
        self.run.inventory[name] = self.run.inventory.get(name, 0) + 1

    def queue_dash(self, target: tuple[int, int]) -> None:
        dx = target[0] - self.next_position[0]
        dy = target[1] - self.next_position[1]
        self.movement_target = target
        self.dash_jump_start = self.next_position
        self.dash_v_position = (
            target[0] - max(-1, min(1, dx)),
            target[1] - max(-1, min(1, dy)),
        )

    def attach_undo_action(self, action: UndoAction) -> None:
        self._undo_action = action

    def commit_command(self, previous_pending: str) -> None:
        self._append_command_undo(
            self.run,
            step_index=self.step_index,
            previous_pending=previous_pending,
            undo_action=self._undo_action,
        )
        self._undo_action = None

    def log(self, message: str) -> None:
        self.run.log(message)
