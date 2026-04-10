from __future__ import annotations

from .base import Ability, AbilityContext


class SilenceAbility(Ability):
    name = "silence"
    commands = ("silence",)

    def execute(self, ctx: AbilityContext) -> None:
        if not ctx.player.consume_inventory(self.name):
            ctx.player.log("SILENCE unavailable.")
            return
        ctx.world.set_silence_ticks(20)
        ctx.player.log("SILENCE injected. Enemies paused for 20 ticks.")
