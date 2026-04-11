from __future__ import annotations

import random
import sys
from collections import deque

from .audio import AudioManager
from .constants import DIRECTIONS, SHOP_ITEMS
from .gameplay import advance_player, create_run, retract_player, tick
from .models import CreditsState, RunState, Segment
from .rendering import Renderer
from .scene_types import GameSession
from .scenes import CreditsScene, MenuScene, ResultScene, RunScene, ShopScene
from .storage import load_save, save_save
from .terminal import TerminalController


class ViskApp:
    def __init__(self) -> None:
        self.session = GameSession(save=load_save())
        self.rng = random.Random()
        self.renderer = Renderer()
        self.audio = AudioManager()
        self.scenes = {
            "menu": MenuScene(self.renderer, self.session),
            "shop": ShopScene(self.renderer, self.session),
            "credits": CreditsScene(self.renderer, self.session),
            "result": ResultScene(self.renderer, self.session),
            "run": RunScene(self.renderer, self.session),
        }

    @property
    def save(self):
        return self.session.save

    @property
    def state(self) -> str:
        return self.session.state

    @state.setter
    def state(self, value: str) -> None:
        self.session.state = value

    @property
    def run(self) -> RunState | None:
        return self.session.run

    @run.setter
    def run(self, value: RunState | None) -> None:
        self.session.run = value

    @property
    def credits(self) -> CreditsState | None:
        return self.session.credits

    @credits.setter
    def credits(self, value: CreditsState | None) -> None:
        self.session.credits = value

    @property
    def shop_cursor(self) -> int:
        return self.session.shop_cursor

    @shop_cursor.setter
    def shop_cursor(self, value: int) -> None:
        self.session.shop_cursor = value

    def current_scene(self):
        return self.scenes.get(self.state, self.scenes["menu"])

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
        width, height = self.renderer.get_viewport_size()
        start = Segment(width // 2, self.renderer.credits_start_y(width, height), " ")
        self.credits = CreditsState(body=deque([start]), width=width, height=height)
        self.state = "credits"

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
                    (
                        len(self.credits.body),
                        self.credits.direction,
                        self.credits.pending_command[:-1],
                    )
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
        if (nx, ny) in {
            (segment.x, segment.y) for segment in list(self.credits.body)[:-1]
        }:
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
            self.run_timed_tick(
                player_action=lambda: retract_player(self.run),
                reason="key",
            )
            return
        if len(key) == 1 and key.isprintable() and not key.isspace():
            self.run_timed_tick(
                player_action=lambda key=key: advance_player(self.run, key),
                reason="key",
            )

    def run_timed_tick(
        self,
        *,
        player_action,
        reason: str,
    ) -> None:
        if self.run is None:
            return
        tick(
            self.run,
            self.save,
            self.rng,
            player_action=player_action,
            reason=reason,
        )

    def present_current_scene(self) -> None:
        self.audio.sync_music(self.state, self.save.audio_enabled)
        frame = self.renderer.present_scene(self.current_scene())
        if frame:
            sys.stdout.write(frame)
            sys.stdout.flush()

    def run_loop(self) -> None:
        try:
            with TerminalController() as terminal:
                while True:
                    self.present_current_scene()
                    timeout = 0.08
                    if (
                        self.state == "run"
                        and self.run is not None
                        and not self.run.game_over
                        and self.run.hardcore
                    ):
                        timeout = 0.45
                    elif self.state == "run":
                        timeout = 0.12
                    key = terminal.read_key(timeout)
                    if key is None:
                        if (
                            self.state == "run"
                            and self.run is not None
                            and not self.run.game_over
                            and self.run.hardcore
                        ):
                            self.run_timed_tick(
                                player_action=None,
                                reason="idle",
                            )
                            if self.run is not None and self.run.game_over:
                                self.finish_run()
                        continue

                    self.audio.play_keystroke(True)
                    if self.state == "menu":
                        self.handle_menu_key(key)
                        continue
                    if self.state == "shop":
                        self.handle_shop_key(key)
                        continue
                    if self.state == "result":
                        self.handle_result_key(key)
                        continue
                    if self.state == "credits":
                        self.handle_credits_key(key)
                        continue
                    if self.state == "run":
                        self.handle_run_key(key)
                        if self.run is not None and self.run.game_over:
                            self.finish_run()
        finally:
            self.audio.shutdown()
