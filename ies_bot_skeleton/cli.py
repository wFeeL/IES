from __future__ import annotations

import argparse
from typing import Sequence

from .offline.cli import main as offline_main
from .online.main import main as online_main


def _run_online() -> int:
    try:
        online_main()
    except RuntimeError as exc:
        import sys

        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_offline(args: Sequence[str]) -> int:
    return offline_main(list(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ies",
        description="Unified launcher for online (ips) and offline modes.",
    )
    parser.add_argument(
        "mode",
        choices=("online", "offline", "bot", "lottool"),
        help=(
            "Mode: 'online' for ips runtime, 'offline' for lot utilities. "
            "'bot' and 'lottool' are compatibility aliases."
        ),
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

    if ns.mode in ("online", "bot"):
        if mode_args:
            if ns.mode == "online" and mode_args == ["run"]:
                mode_args = []
            else:
                raise SystemExit(f"'{ns.mode}' mode does not accept extra args: {' '.join(mode_args)}")
        return _run_online()

    if ns.mode == "lottool":
        return _run_offline(["lottool", *mode_args])

    if ns.mode == "offline":
        if not mode_args:
            raise SystemExit("offline mode requires a subcommand: lottool | fill-lots")
        return _run_offline(mode_args)

    raise SystemExit(f"Unsupported mode: {ns.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
