from __future__ import annotations

from .base import Ability, AbilityContext


class ZapAbility(Ability):
    name = "zap"
    commands = ("zap",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("ZAP unavailable.")
            return
        enemy = ctx.world.closest_enemy()
        if enemy is None:
            ctx.player.log("ZAP found no target.")
            ctx.player.refund_inventory(self.name)
            return
        ctx.world.kill_enemy(enemy, "zap")
