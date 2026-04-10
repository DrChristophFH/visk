from __future__ import annotations

from ..models import UndoAction
from .base import Ability, AbilityContext


class MineAbility(Ability):
    name = "mine"
    commands = ("mine",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("MINE unavailable.")
            return
        object_id = ctx.world.spawn_mine_at_player(ctx.player)
        ctx.player.attach_undo_action(
            UndoAction(
                kind="remove_world_object",
                object_id=object_id,
                inventory_name=self.name,
                label="MINE",
            )
        )
        ctx.player.log("MINE deployed.")
