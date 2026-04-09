from __future__ import annotations

import random
import shutil
import sys

from .constants import SHOP_ITEMS
from .gameplay import advance_player, create_run, retract_player, tick, use_ability
from .models import RunState
from .rendering import Renderer
from .storage import load_save, save_save
from .terminal import TerminalController


class ViskApp:
    def __init__(self) -> None:
        self.save = load_save()
        self.rng = random.Random()
        self.state = "menu"
        self.run: RunState | None = None
        self.shop_cursor = 0
        self.renderer = Renderer()

    def new_run(self) -> None:
        self.run = create_run(self.save)
        self.state = "run"
        self.renderer.reset_run_cache()

    def finish_run(self) -> None:
        if self.run is None:
            return
        if self.run.extracted:
            reward = self.run.bytes_collected + 40 * self.run.kills
            self.save.banked_bytes += reward
            self.save.streak += 1
        else:
            self.save.streak = 0
        save_save(self.save)
        self.state = "result"
        self.renderer.reset_run_cache()

    def handle_menu_key(self, key: str) -> None:
        if key in ("n", "N", "ENTER"):
            self.new_run()
        elif key in ("s", "S"):
            self.state = "shop"
        elif key in ("h", "H"):
            self.save.hardcore = not self.save.hardcore
            save_save(self.save)
        elif key in ("q", "Q", "ESC"):
            raise SystemExit

    def handle_shop_key(self, key: str) -> None:
        if key == "UP":
            self.shop_cursor = (self.shop_cursor - 1) % len(SHOP_ITEMS)
        elif key == "DOWN":
            self.shop_cursor = (self.shop_cursor + 1) % len(SHOP_ITEMS)
        elif key in ("ENTER", " "):
            self.purchase_selected()
        elif key in ("m", "M", "ESC"):
            self.state = "menu"

    def purchase_selected(self) -> None:
        item = SHOP_ITEMS[self.shop_cursor]
        level = self.save.upgrades[item["id"]]
        cost = item["base_cost"] + level * 90
        if self.save.banked_bytes < cost:
            return
        self.save.banked_bytes -= cost
        self.save.upgrades[item["id"]] += 1
        save_save(self.save)

    def handle_result_key(self, key: str) -> None:
        if key in ("ENTER", "n", "N"):
            self.new_run()
        elif key in ("m", "M", "ESC"):
            self.state = "menu"

    def handle_run_key(self, key: str) -> None:
        if self.run is None or self.run.game_over:
            return
        if key == "ESC":
            self.state = "menu"
            return
        if key == "BACKSPACE":
            tick(self.run, self.save, self.rng, player_action=lambda: retract_player(self.run), reason="key")
            return
        if len(key) == 1 and key.isprintable() and not key.isspace():
            tick(self.run, self.save, self.rng, player_action=lambda key=key: advance_player(self.run, key), reason="key")

    def run_loop(self) -> None:
        with TerminalController() as terminal:
            while True:
                cols, rows = shutil.get_terminal_size((120, 38))
                cols = max(cols, 60)
                rows = max(rows, 22)
                frame = self.renderer.render_frame(self.state, self.run, self.save, self.shop_cursor, cols, rows)
                if frame:
                    sys.stdout.write(frame)
                    sys.stdout.flush()

                timeout = 0.08
                if self.state == "run" and self.run is not None and not self.run.game_over and self.run.hardcore:
                    timeout = 0.45
                elif self.state == "run":
                    timeout = 0.12
                key = terminal.read_key(timeout)
                if key is None:
                    if self.state == "run" and self.run is not None and not self.run.game_over and self.run.hardcore:
                        tick(self.run, self.save, self.rng, player_action=None, reason="idle")
                    continue
                if self.state == "menu":
                    self.handle_menu_key(key)
                elif self.state == "shop":
                    self.handle_shop_key(key)
                elif self.state == "result":
                    self.handle_result_key(key)
                elif self.state == "run":
                    self.handle_run_key(key)
                    if self.run is not None and self.run.game_over:
                        self.finish_run()

    def smoke_test(self) -> None:
        self.new_run()
        assert self.run is not None
        for char in "right":
            advance_player(self.run, char)
        use_ability(self.run, "ping")
        frame = self.renderer.render_run(self.run, 120, 40).render_full()
        print("VISK smoke test ok")
        print(frame[:400].replace("\x1b", "<ESC>"))
