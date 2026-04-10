from __future__ import annotations

from ..rendering import Renderer
from ..scene_types import GameSession, SceneRenderResult


class BaseScene:
    name = "base"

    def __init__(self, renderer: Renderer, session: GameSession) -> None:
        self.renderer = renderer
        self.session = session

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        return SceneRenderResult()
