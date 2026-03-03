from __future__ import annotations

import sys


def ies_online() -> None:
    from .online.main import main as online_main

    raise SystemExit(online_main(sys.argv[1:]))


def ies_lottool() -> None:
    from .offline.cli import main as offline_main

    raise SystemExit(offline_main(["lottool", *sys.argv[1:]]))


def ies_fill_lots() -> None:
    from .offline.cli import main as offline_main

    raise SystemExit(offline_main(["fill-lots", *sys.argv[1:]]))


__all__ = ["ies_fill_lots", "ies_lottool", "ies_online"]
