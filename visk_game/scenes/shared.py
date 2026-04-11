from __future__ import annotations

from ..models import SaveData, Theme
from ..utils import Colors


def save_signature(save: SaveData) -> tuple[object, ...]:
    return (
        save.banked_bytes,
        save.streak,
        save.hardcore,
        save.audio_enabled,
        tuple(sorted(save.upgrades.items())),
    )


def player_segment_color(
    theme: Theme,
    index: int,
    body_length: int,
    *,
    infected: bool,
    pickup_color: tuple[int, int, int] | None = None,
) -> tuple[int, int, int]:
    if pickup_color is not None:
        color = pickup_color
    else:
        fade_cap = 0.78
        if body_length <= 1:
            color = theme["player"]
        else:
            recentness = index / max(1, body_length - 1)
            fade_amount = (1.0 - recentness) * fade_cap
            color = Colors.mix(theme["player"], theme["muted"], fade_amount)
    if infected:
        color = Colors.mix(color, theme["enemy"], 0.6)
    return color
