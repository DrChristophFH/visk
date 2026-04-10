from __future__ import annotations

from ..constants import SHOP_ITEMS, THEMES
from ..models import Canvas
from ..scene_types import SceneLayer, SceneRenderResult
from ..utils import wrap_lines
from .base import BaseScene
from .shared import save_signature


class ShopScene(BaseScene):
    name = "shop"

    def render(self, cols: int, rows: int) -> SceneRenderResult:
        layer = SceneLayer(
            key="shop.base",
            z_index=0,
            cache_key=(
                "shop.base",
                cols,
                rows,
                self.session.shop_cursor,
                save_signature(self.session.save),
            ),
            build_canvas=lambda cols=cols, rows=rows: self.build_canvas(cols, rows),
        )
        return SceneRenderResult([layer])

    def build_canvas(self, cols: int, rows: int) -> Canvas:
        save = self.session.save
        theme = THEMES[0]
        canvas = Canvas(cols, rows, theme["bg"])
        canvas.fill_noise(
            0, 0, cols, rows, base=theme["bg"], alt=theme["bg_alt"], seed=7001
        )
        panel_w = min(cols - 6, 90)
        panel_h = min(rows - 4, 20)
        x = (cols - panel_w) // 2
        y = (rows - panel_h) // 2
        self.renderer.draw_panel(
            canvas, x, y, panel_w, panel_h, title="BYTE MARKET", theme=theme
        )
        canvas.text(
            x + 3,
            y + 2,
            f"Banked bytes: {save.banked_bytes}",
            fg_color=theme["player"],
            bold=True,
        )
        canvas.text(
            x + 3, y + 3, "Press ENTER to buy. ESC to return.", fg_color=theme["muted"]
        )
        row_y = y + 5
        for idx, item in enumerate(SHOP_ITEMS):
            level = save.upgrades[item["id"]]
            cost = item["base_cost"] + level * 90
            selected = idx == self.session.shop_cursor
            tone = theme["pickup"] if selected else theme["player"]
            prefix = ">" if selected else " "
            canvas.text(
                x + 3,
                row_y,
                f"{prefix} {item['name']}  L{level}  COST {cost}",
                fg_color=tone,
                bold=selected,
            )
            for line_idx, line in enumerate(
                wrap_lines(item["description"], panel_w - 10)
            ):
                canvas.text(x + 6, row_y + 1 + line_idx, line, fg_color=theme["muted"])
            row_y += 4
        return canvas
