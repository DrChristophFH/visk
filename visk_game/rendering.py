from __future__ import annotations

import shutil

from .constants import CREDITS_PAGE_LINES
from .models import Canvas
from .scene_types import LayerCacheEntry, Scene
from .utils import wrap_lines


class Renderer:
    def __init__(self) -> None:
        self.last_canvas: Canvas | None = None
        self.layer_cache: dict[str, LayerCacheEntry] = {}
        self.viewport_cols = 120
        self.viewport_rows = 38

    def reset_run_cache(self) -> None:
        run_keys = [key for key in self.layer_cache if key.startswith("run.")]
        for key in run_keys:
            self.layer_cache.pop(key, None)

    def get_viewport_size(self) -> tuple[int, int]:
        cols, rows = shutil.get_terminal_size((120, 38))
        self.viewport_cols = max(cols, 60)
        self.viewport_rows = max(rows, 22)
        return self.viewport_cols, self.viewport_rows

    def credits_start_y(self, cols: int, rows: int) -> int:
        max_text_width = max(20, cols - 6)
        text_height = sum(
            len(wrap_lines(line, max_text_width)) for line, _, _ in CREDITS_PAGE_LINES
        )
        return max(0, min(rows - 2, 2 + text_height + 1))

    def draw_panel(
        self,
        canvas: Canvas,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        title: str,
        theme,
    ) -> None:
        bg_color = theme["bg_alt"]
        wall = theme["wall"]
        for yy in range(y, y + height):
            for xx in range(x, x + width):
                canvas.put(xx, yy, " ", bg_color=bg_color)
        for xx in range(x, x + width):
            canvas.put(xx, y, "─", fg_color=wall, bg_color=bg_color)
            canvas.put(xx, y + height - 1, "─", fg_color=wall, bg_color=bg_color)
        for yy in range(y, y + height):
            canvas.put(x, yy, "│", fg_color=wall, bg_color=bg_color)
            canvas.put(x + width - 1, yy, "│", fg_color=wall, bg_color=bg_color)
        for corner in (
            (x, y),
            (x + width - 1, y),
            (x, y + height - 1),
            (x + width - 1, y + height - 1),
        ):
            canvas.put(
                corner[0], corner[1], "┼", fg_color=wall, bg_color=bg_color, bold=True
            )
        canvas.text(
            x + 2,
            y,
            f" {title} ",
            fg_color=theme["pickup"],
            bg_color=bg_color,
            bold=True,
        )

    def resolve_layer_canvas(self, layer) -> Canvas:
        if layer.cache_key is None:
            return layer.build_canvas()
        cached = self.layer_cache.get(layer.key)
        if cached is not None and cached.cache_key == layer.cache_key:
            return cached.canvas
        canvas = layer.build_canvas()
        self.layer_cache[layer.key] = LayerCacheEntry(layer.cache_key, canvas)
        return canvas

    def apply_layer(self, target: Canvas, layer: Canvas) -> None:
        for y in range(min(target.height, layer.height)):
            for x in range(min(target.width, layer.width)):
                cell = layer.cells[y][x]
                if cell.is_transparent():
                    continue
                target.cells[y][x] = cell

    def compose_layers(self, scene: Scene, cols: int, rows: int) -> Canvas:
        render_result = scene.render(cols, rows)
        canvas = Canvas.transparent(cols, rows)
        for layer in sorted(render_result.layers, key=lambda item: item.z_index):
            self.apply_layer(canvas, self.resolve_layer_canvas(layer))
        return canvas

    def present_scene(self, scene: Scene) -> str:
        cols, rows = self.get_viewport_size()
        canvas = self.compose_layers(scene, cols, rows)
        frame = canvas.render_diff(self.last_canvas)
        self.last_canvas = canvas
        return frame
