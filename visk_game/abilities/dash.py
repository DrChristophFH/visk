from __future__ import annotations

from .base import Ability, AbilityContext


class DashAbility(Ability):
    name = "dash"
    commands = ("dash",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("DASH unavailable.")
            return
        target = ctx.world.find_dash_target(ctx.player.direction(), 4)
        if target is None:
            ctx.player.log("DASH obstructed.")
            ctx.player.refund_inventory(self.name)
            return
        ctx.player.queue_dash(target)
        ctx.player.log("DASH executed.")
