from __future__ import annotations

from ..constants import RUN_ART, THEMES
from ..models import Canvas
from ..scene_types import SceneLayer, SceneRenderResult
from ..utils import mix, wrap_lines
from .base import BaseScene
from .shared import save_signature


class MenuScene(BaseScene):
    name = "menu"

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        layer = SceneLayer(
            key="menu.base",
            z_index=0,
            cache_key=("menu.base", cols, rows, save_signature(self.session.save)),
            build_canvas=lambda cols=cols, rows=rows: self.build_canvas(cols, rows),
        )
        return SceneRenderResult([layer])

    def build_canvas(self, cols: int, rows: int) -> Canvas:
        save = self.session.save
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=2311
        )
        top = max(2, rows // 2 - 12)
        art_width = max(len(line) for line in RUN_ART)
        left = max(2, (cols - art_width) // 2)
        for i, line in enumerate(RUN_ART):
            canvas.text(
                left,
                top + i,
                line,
                fg_color=mix(
                    theme["accent"], theme["player"], i / max(1, len(RUN_ART) - 1)
                ),
                bold=True,
            )
        summary = (
            "Type directly into the game, spell up/down/left/right to turn, "
            "use BACKSPACE to rewind. Typing it out is the only way."
        )
        for idx, line in enumerate(wrap_lines(summary, max(30, cols - 12))):
            canvas.text(6, top + 8 + idx, line, fg_color=theme["muted"])
        options = [
            "[N] New Run",
            "[S] Shop",
            "[C] Credits",
            f"[A] Music: {'ON' if save.audio_enabled else 'OFF'}",
            f"[H] Hardcore: {'ON' if save.hardcore else 'OFF'}",
            "[Q] Quit",
        ]
        for idx, option in enumerate(options):
            highlight = idx in (3, 4)
            canvas.text(
                8,
                top + 12 + idx * 2,
                option,
                fg_color=theme["pickup"] if highlight else theme["player"],
                bold=True,
            )
        stats = [
            f"Banked bytes: {save.banked_bytes}",
            f"Win streak: {save.streak}",
            (
                "Upgrades: DASH "
                f"{save.upgrades['dash_cache']} | PING {save.upgrades['ping_cache']} | "
                f"MAGNET {save.upgrades['magnet']} | FOCUS {save.upgrades['focus']}"
            ),
        ]
        for idx, line in enumerate(stats):
            canvas.text(6, rows - 5 + idx, line[: cols - 10], fg_color=theme["accent"])
        return canvas
