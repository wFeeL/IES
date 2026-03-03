from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .lots import fill_lots, summarize_report
from .lottool import run_lottool_cli


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOTS_DIR = ROOT_DIR / "lot_tool" / "data" / "lots"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ies-offline",
        description="Offline mode: lot evaluation and lot data maintenance without ips.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    lottool = sub.add_parser("lottool", help="run lot valuation CLI")
    lottool.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to lottool")

    fill = sub.add_parser("fill-lots", help="normalize and fill existing lot JSON files")
    fill.add_argument("--lots-dir", default=str(DEFAULT_LOTS_DIR))
    fill.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)

    if ns.cmd == "lottool":
        lottool_args = list(ns.args)
        if lottool_args and lottool_args[0] == "--":
            lottool_args = lottool_args[1:]
        return run_lottool_cli(lottool_args)

    if ns.cmd == "fill-lots":
        report = fill_lots(ns.lots_dir, dry_run=bool(ns.dry_run))
        summary, details = summarize_report(report)
        print(summary)
        for line in details:
            print(line)
        if report.files_skipped > 0:
            return 1
        return 0

    raise SystemExit(f"Unsupported command: {ns.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
