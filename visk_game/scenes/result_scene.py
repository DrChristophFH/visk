from __future__ import annotations

from ..constants import THEMES
from ..models import Canvas
from ..scene_types import SceneLayer, SceneRenderResult
from .base import BaseScene
from .shared import save_signature


class ResultScene(BaseScene):
    name = "result"

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        layer = SceneLayer(
            key="result.base",
            z_index=0,
            cache_key=(
                "result.base",
                cols,
                rows,
                save_signature(self.session.save),
                self.result_signature(),
            ),
            build_canvas=lambda cols=cols, rows=rows: self.build_canvas(cols, rows),
        )
        return SceneRenderResult([layer])

    def result_signature(self) -> tuple[object, ...] | None:
        run = self.session.run
        if run is None:
            return None
        return (
            run.extracted,
            run.bytes_collected,
            run.kills,
            run.cause,
        )

    def build_canvas(self, cols: int, rows: int) -> Canvas:
        run = self.session.run
        save = self.session.save
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=415
        )
        panel_w = min(cols - 8, 84)
        panel_h = min(rows - 6, 18)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.renderer.draw_panel(
            canvas, x, y, panel_w, panel_h, title="RUN RESULT", theme=theme
        )
        if run is None:
            return canvas
        title = "EXTRACTION COMPLETE" if run.extracted else "SESSION TERMINATED"
        tone = theme["accent"] if run.extracted else theme["enemy"]
        canvas.text(x + 3, y + 2, title, fg_color=tone, bold=True)
        if run.extracted:
            reward = run.bytes_collected + 40 * run.kills
            lines = [
                f"Bytes banked: {reward}",
                f"Enemies deleted: {run.kills}",
                f"Streak: {save.streak}",
            ]
        else:
            lines = [
                f"Cause: {run.cause}",
                f"Bytes lost: {run.bytes_collected}",
                f"Streak reset to: {save.streak}",
            ]
        for idx, line in enumerate(lines):
            canvas.text(x + 3, y + 5 + idx * 2, line, fg_color=theme["player"])
        canvas.text(
            x + 3,
            y + panel_h - 3,
            "[ENTER] new run   [M] menu",
            fg_color=theme["pickup"],
            bold=True,
        )
        return canvas
