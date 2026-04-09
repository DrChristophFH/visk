from __future__ import annotations

import argparse

from .app import ViskApp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VISK terminal roguelite")
    parser.add_argument("--smoke-test", action="store_true", help="run a non-interactive smoke test")
    args = parser.parse_args(argv)
    app = ViskApp()
    if args.smoke_test:
        app.smoke_test()
        return 0
    try:
        app.run_loop()
    except KeyboardInterrupt:
        return 130
    return 0
