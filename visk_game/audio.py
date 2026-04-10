from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame


class AudioManager:
    def __init__(self) -> None:
        self.enabled = False
        self.current_track: str | None = None
        self._rng = random.Random()
        self._keystroke_sounds: list[Any] = []
        self._keystroke_loaded = False
        self._last_keystroke_index: int | None = None
        self._res_dir = Path(__file__).resolve().parent.parent / "res"
        self._music_paths = {
            "menu": self._res_dir / "Arcade.ogg",
            "run": self._res_dir / "Echoes of Eternity.ogg",
        }
        self._keystroke_config_path = self._res_dir / "config.json"
        self._ensure_ready()

    def _ensure_ready(self) -> bool:
        if self.enabled:
            return True
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.set_num_channels(max(16, pygame.mixer.get_num_channels()))
        except Exception:
            return False
        self.enabled = True
        self._load_keystroke_sounds()
        return True

    def _load_keystroke_sounds(self) -> None:
        if self._keystroke_loaded or pygame is None:
            return
        self._keystroke_loaded = True
        if not self._keystroke_config_path.exists():
            return
        try:
            config = json.loads(self._keystroke_config_path.read_text(encoding="utf-8"))
            sound_name = config.get("sound", "oreo.ogg")
            sound_path = self._res_dir / sound_name
            defines = config.get("defines", {})
            if not sound_path.exists() or not isinstance(defines, dict):
                return
            base_sound = pygame.mixer.Sound(str(sound_path))
            samples = pygame.sndarray.array(base_sound)
            mixer_init = pygame.mixer.get_init()
            if mixer_init is None:
                return
            sample_rate = mixer_init[0]
            total_frames = int(samples.shape[0])
            for define in defines.values():
                if not isinstance(define, list) or len(define) != 2:
                    continue
                start_ms, duration_ms = define
                if not isinstance(start_ms, int | float) or not isinstance(
                    duration_ms, int | float
                ):
                    continue
                if duration_ms <= 0:
                    continue
                start_frame = max(0, int(sample_rate * start_ms / 1000))
                frame_count = max(1, int(sample_rate * duration_ms / 1000))
                end_frame = min(total_frames, start_frame + frame_count)
                if end_frame <= start_frame:
                    continue
                clip = samples[start_frame:end_frame].copy()
                sound = pygame.sndarray.make_sound(clip)
                sound.set_volume(0.45)
                self._keystroke_sounds.append(sound)
        except Exception:
            self._keystroke_sounds.clear()

    def stop_music(self) -> None:
        if not self.enabled or pygame is None:
            self.current_track = None
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self.current_track = None

    def sync_music(self, state: str, enabled: bool) -> None:
        if not enabled:
            self.stop_music()
            return
        target = "run" if state == "run" else "menu"
        if self.current_track == target:
            return
        if not self._ensure_ready():
            return
        path = self._music_paths[target]
        if not path.exists():
            return
        assert pygame is not None
        try:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play(-1)
        except Exception:
            return
        self.current_track = target

    def play_keystroke(self, enabled: bool) -> None:
        if not enabled:
            return
        if not self._ensure_ready() or not self._keystroke_sounds:
            return
        if len(self._keystroke_sounds) == 1:
            index = 0
        else:
            index = self._rng.randrange(len(self._keystroke_sounds) - 1)
            if (
                self._last_keystroke_index is not None
                and index >= self._last_keystroke_index
            ):
                index += 1
        self._last_keystroke_index = index
        try:
            self._keystroke_sounds[index].play()
        except Exception:
            return

    def shutdown(self) -> None:
        if not self.enabled or pygame is None:
            return
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        self.enabled = False
        self.current_track = None
        self._keystroke_sounds.clear()
        self._keystroke_loaded = False
        self._last_keystroke_index = None
