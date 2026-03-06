from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
LOTTOOL_SRC = ROOT_DIR / "lot_tool" / "src"


def ensure_lottool_path() -> None:
    src = str(LOTTOOL_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def run_lottool_cli(args: Sequence[str]) -> int:
    ensure_lottool_path()
    from lottool.cli import main as lottool_main

    lottool_main(list(args))
    return 0
