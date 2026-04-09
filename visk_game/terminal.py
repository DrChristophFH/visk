from __future__ import annotations

import ctypes
import os
import select
import sys
import time


class TerminalController:
    def __init__(self) -> None:
        self.is_windows = os.name == "nt"
        self.original_termios = None
        self.stdin_fd = None
        self.console_mode = None
        self.in_alt_screen = False
        self.cursor_hidden = False

    def __enter__(self) -> "TerminalController":
        sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[?25l")
        sys.stdout.flush()
        self.in_alt_screen = True
        self.cursor_hidden = True
        if self.is_windows:
            self._enter_windows()
        else:
            self._enter_posix()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.is_windows:
            self._exit_windows()
        else:
            self._exit_posix()
        if self.cursor_hidden or self.in_alt_screen:
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        self.cursor_hidden = False
        self.in_alt_screen = False

    def _enter_windows(self) -> None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            self.console_mode = mode.value
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)

    def _exit_windows(self) -> None:
        if self.console_mode is not None:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            kernel32.SetConsoleMode(handle, self.console_mode)

    def _enter_posix(self) -> None:
        import termios
        import tty

        self.stdin_fd = sys.stdin.fileno()
        self.original_termios = termios.tcgetattr(self.stdin_fd)
        tty.setcbreak(self.stdin_fd)

    def _exit_posix(self) -> None:
        if self.original_termios is None or self.stdin_fd is None:
            return
        import termios

        termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.original_termios)

    def read_key(self, timeout: float | None) -> str | None:
        if self.is_windows:
            import msvcrt

            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        special = msvcrt.getwch()
                        mapping = {"K": "LEFT", "M": "RIGHT", "H": "UP", "P": "DOWN"}
                        return mapping.get(special, None)
                    if ch == "\r":
                        return "ENTER"
                    if ch == "\x08":
                        return "BACKSPACE"
                    if ch == "\t":
                        return "TAB"
                    if ch == "\x1b":
                        return "ESC"
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    return ch
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([sys.stdin], [], [], remaining)
            if not ready:
                return None
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ready, _, _ = select.select([sys.stdin], [], [], 0.0001)
                if ready:
                    next1 = sys.stdin.read(1)
                    next2 = sys.stdin.read(1) if select.select([sys.stdin], [], [], 0.0001)[0] else ""
                    mapping = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}
                    return mapping.get(next1 + next2, "ESC")
                return "ESC"
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch in ("\x7f", "\b"):
                return "BACKSPACE"
            if ch == "\t":
                return "TAB"
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch
