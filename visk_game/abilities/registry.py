from __future__ import annotations

from .bomb import BombAbility
from .dash import DashAbility
from .mine import MineAbility
from .ping import PingAbility
from .silence import SilenceAbility
from .zap import ZapAbility

ABILITIES = (
    ZapAbility(),
    BombAbility(),
    MineAbility(),
    SilenceAbility(),
    PingAbility(),
    DashAbility(),
)

ABILITY_COMMANDS = tuple(
    command for ability in ABILITIES for command in ability.commands
)
ABILITY_BY_COMMAND = {
    command: ability for ability in ABILITIES for command in ability.commands
}


def get_ability(command: str):
    return ABILITY_BY_COMMAND.get(command)
