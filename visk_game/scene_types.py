from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Protocol

from .models import Canvas, CreditsState, RunState, SaveData


@dataclass
class GameSession:
    save: SaveData
    state: str = "menu"
    run: RunState | None = None
    credits: CreditsState | None = None
    shop_cursor: int = 0


@dataclass(frozen=True)
class SceneLayer:
    key: str
    z_index: int
    cache_key: Hashable | None
    build_canvas: Callable[[], Canvas]


@dataclass
class LayerCacheEntry:
    cache_key: Hashable
    canvas: Canvas


@dataclass
class SceneRenderResult:
    layers: list[SceneLayer] = field(default_factory=list)


class Scene(Protocol):
    name: str

    def render(self, cols: int, rows: int) -> SceneRenderResult: ...
