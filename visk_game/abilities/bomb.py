from __future__ import annotations

from ..models import UndoAction
from .base import Ability, AbilityContext


class BombAbility(Ability):
    name = "bomb"
    commands = ("bomb",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("BOMB unavailable.")
            return
        object_id = ctx.world.spawn_bomb_from_player(ctx.player)
        ctx.player.attach_undo_action(
            UndoAction(
                kind="remove_world_object",
                object_id=object_id,
                inventory_name=self.name,
                label="BOMB",
            )
        )
        ctx.player.log("BOMB armed. Fuse: 5.")
