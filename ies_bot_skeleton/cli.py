from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parent
LOTTOOL_SRC = ROOT_DIR / "lot_tool" / "src"


def _ensure_lottool_path() -> None:
    src = str(LOTTOOL_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def _run_bot() -> int:
    from main import main as bot_main

    try:
        bot_main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_lottool(args: Sequence[str]) -> int:
    _ensure_lottool_path()
    from lottool.cli import main as lottool_main

    lottool_main(list(args))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ies",
        description="Unified launcher for IES bot and lot valuation tool.",
    )
    parser.add_argument(
        "mode",
        choices=("bot", "lottool"),
        help="Launch mode: 'bot' for stand controller, 'lottool' for lot valuation CLI.",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to the selected mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    mode_args = list(ns.args)
    if mode_args and mode_args[0] == "--":
        mode_args = mode_args[1:]

    if ns.mode == "bot":
        if mode_args:
            raise SystemExit(f"'bot' mode does not accept extra args: {' '.join(mode_args)}")
        return _run_bot()

    if ns.mode == "lottool":
        return _run_lottool(mode_args)

    raise SystemExit(f"Unsupported mode: {ns.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
