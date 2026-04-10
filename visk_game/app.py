from __future__ import annotations

from collections import deque
import random
import shutil
import sys

from .audio import AudioManager
from .constants import CREDITS_PAGE_LINES, DIRECTIONS, SHOP_ITEMS
from .gameplay import advance_player, create_run, retract_player, tick, use_ability
from .models import CreditsState, RunState, Segment
from .rendering import Renderer
from .storage import load_save, save_save
from .terminal import TerminalController
from .utils import wrap_lines


class ViskApp:
    def __init__(self) -> None:
        self.save = load_save()
        self.rng = random.Random()
        self.state = "menu"
        self.run: RunState | None = None
        self.credits: CreditsState | None = None
        self.shop_cursor = 0
        self.renderer = Renderer()
        self.audio = AudioManager()
        self.last_cols = 120
        self.last_rows = 38

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

    def open_credits(self) -> None:
        width = self.last_cols
        height = self.last_rows
        start = Segment(width // 2, self.credits_start_y(width, height), " ")
        self.credits = CreditsState(body=deque([start]), width=width, height=height)
        self.state = "credits"

    def credits_start_y(self, cols: int, rows: int) -> int:
        max_text_width = max(20, cols - 6)
        text_height = sum(
            len(wrap_lines(line, max_text_width)) for line, _, _ in CREDITS_PAGE_LINES
        )
        return max(0, min(rows - 2, 2 + text_height + 1))

    def sync_credits_viewport(self, cols: int, rows: int) -> None:
        if self.credits is None:
            return
        self.credits.width = cols
        self.credits.height = rows
        if len(self.credits.body) == 1 and self.credits.body[0].ch == " ":
            self.credits.body[0].x = cols // 2
            self.credits.body[0].y = self.credits_start_y(cols, rows)
        for segment in self.credits.body:
            segment.x = max(0, min(cols - 1, segment.x))
            segment.y = max(0, min(rows - 1, segment.y))

    def handle_menu_key(self, key: str) -> None:
        if key in ("n", "N", "ENTER"):
            self.new_run()
        elif key in ("s", "S"):
            self.state = "shop"
        elif key in ("c", "C"):
            self.open_credits()
        elif key in ("a", "A"):
            self.save.audio_enabled = not self.save.audio_enabled
            save_save(self.save)
            if not self.save.audio_enabled:
                self.audio.stop_music()
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

    def close_credits(self) -> None:
        self.state = "menu"
        self.credits = None

    def resolve_credits_inline_command(self) -> bool:
        if self.credits is None or not self.credits.pending_command:
            return False
        suffix = self.credits.pending_command.lower()
        commands = ("menu", "exit", *DIRECTIONS.keys())
        for command in sorted(commands, key=len, reverse=True):
            if not suffix.endswith(command):
                continue
            if command in DIRECTIONS:
                self.credits.direction_undos.append(
                    (len(self.credits.body), self.credits.direction, self.credits.pending_command[:-1])
                )
                self.credits.direction = command
                self.credits.pending_command = ""
                return False
            self.close_credits()
            return True
        return False

    def advance_credits(self, typed_char: str) -> None:
        if self.credits is None:
            return
        head = self.credits.head
        dx, dy = DIRECTIONS[self.credits.direction]
        nx = head.x + dx
        ny = head.y + dy
        if not (0 <= nx < self.credits.width and 0 <= ny < self.credits.height):
            return
        if (nx, ny) in {(segment.x, segment.y) for segment in list(self.credits.body)[:-1]}:
            return
        head.ch = typed_char
        self.credits.pending_command += typed_char
        if self.resolve_credits_inline_command():
            return
        self.credits.body.append(Segment(nx, ny, " "))

    def retract_credits(self) -> None:
        if self.credits is None or len(self.credits.body) <= 1:
            return
        current_step = len(self.credits.body) - 1
        if self.credits.pending_command:
            self.credits.pending_command = self.credits.pending_command[:-1]
        elif (
            self.credits.direction_undos
            and self.credits.direction_undos[-1][0] == current_step
        ):
            _, previous_direction, previous_pending = self.credits.direction_undos.pop()
            self.credits.direction = previous_direction
            self.credits.pending_command = previous_pending
        else:
            return
        self.credits.body.pop()
        self.credits.body[-1].ch = " "

    def handle_credits_key(self, key: str) -> None:
        if self.credits is None:
            return
        if key == "ESC":
            self.close_credits()
            return
        if key == "BACKSPACE":
            self.retract_credits()
            return
        if len(key) == 1 and key.isprintable() and not key.isspace():
            self.advance_credits(key)

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
        try:
            with TerminalController() as terminal:
                while True:
                    self.audio.sync_music(self.state, self.save.audio_enabled)
                    cols, rows = shutil.get_terminal_size((120, 38))
                    cols = max(cols, 60)
                    rows = max(rows, 22)
                    self.last_cols = cols
                    self.last_rows = rows
                    if self.state == "credits":
                        self.sync_credits_viewport(cols, rows)
                    frame = self.renderer.render_frame(
                        self.state,
                        self.run,
                        self.credits,
                        self.save,
                        self.shop_cursor,
                        cols,
                        rows,
                    )
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
                    self.audio.play_keystroke(True)
                    if self.state == "menu":
                        self.handle_menu_key(key)
                    elif self.state == "shop":
                        self.handle_shop_key(key)
                    elif self.state == "result":
                        self.handle_result_key(key)
                    elif self.state == "credits":
                        self.handle_credits_key(key)
                    elif self.state == "run":
                        self.handle_run_key(key)
                        if self.run is not None and self.run.game_over:
                            self.finish_run()
        finally:
            self.audio.shutdown()
