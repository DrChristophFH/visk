from __future__ import annotations

import time

from ..constants import CREDITS_PAGE_LINES, THEMES
from ..models import Canvas
from ..scene_types import SceneLayer, SceneRenderResult
from ..utils import arrow_for_direction, invert_colors, wrap_lines
from .base import BaseScene
from .shared import player_segment_color


class CreditsScene(BaseScene):
    name = "credits"

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        layers = [
            SceneLayer(
                key="credits.base",
                z_index=0,
                cache_key=("credits.base", cols, rows),
                build_canvas=lambda cols=cols, rows=rows: self.build_base_canvas(
                    cols, rows
                ),
            ),
            SceneLayer(
                key="credits.overlay",
                z_index=1,
                cache_key=None,
                build_canvas=lambda cols=cols, rows=rows: self.build_overlay_canvas(
                    cols, rows
                ),
            ),
        ]
        return SceneRenderResult(layers)

    def build_base_canvas(self, cols: int, rows: int) -> Canvas:
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=9113
        )
        max_text_width = max(20, cols - 6)
        text_block_width = min(
            max((len(line) for line, _, _ in CREDITS_PAGE_LINES), default=20),
            max_text_width,
        )
        text_x = max(2, (cols - text_block_width) // 2)
        line_y = 2
        for line, color_key, bold in CREDITS_PAGE_LINES:
            for wrapped in wrap_lines(line, max_text_width):
                canvas.text(
                    text_x,
                    line_y,
                    wrapped,
                    fg_color=theme[color_key],
                    bold=bold,
                )
                line_y += 1
        return canvas

    def build_overlay_canvas(self, cols: int, rows: int) -> Canvas:
        canvas = Canvas.transparent(cols, rows)
        credits = self.session.credits
        if credits is None:
            return canvas
        theme = THEMES[0]
        for idx, segment in enumerate(credits.body):
            color = player_segment_color(theme, idx, len(credits.body), infected=False)
            canvas.put(
                segment.x,
                segment.y,
                segment.ch or " ",
                fg_color=color,
                bg_color=theme["bg"],
            )
        blink = int(time.monotonic() * 2.6) % 2 == 0
        cursor_fg, cursor_bg = invert_colors(theme["player"], theme["bg"], blink)
        canvas.put(
            credits.head.x,
            credits.head.y,
            arrow_for_direction(credits.direction),
            fg_color=cursor_fg,
            bg_color=cursor_bg,
        )
        return canvas
