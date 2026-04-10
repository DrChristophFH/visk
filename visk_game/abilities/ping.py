from __future__ import annotations

from .base import Ability, AbilityContext

PING_DRAW_DISTANCE = 12
PING_DURATION_TICKS = 18


class PingAbility(Ability):
    name = "ping"
    commands = ("ping_exit",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("PING unavailable.")
            return
        target = ctx.world.ping_exit_target()
        if target is None:
            ctx.player.log(f"{ctx.command.upper()} found no target.")
            ctx.player.refund_inventory(self.name)
            return
        path = ctx.world.trace_line(
            ctx.player.head_position(), target, PING_DRAW_DISTANCE
        )
        ctx.world.spawn_ping_trace(path, PING_DURATION_TICKS)
        ctx.player.log(f"{ctx.command.upper()} traced extraction.")
