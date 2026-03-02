from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .io.load_json import load_json, load_lot, load_state
from .io.forecast_loader import load_forecasts
from .model.types import ObjectItem
from .scoring.marginal import marginal_value
from .scoring.scenarios import summarize_delta
from .auction.ev import ev_allpay, recommended_bid_range
from .report.pretty import fmt_delta


LOT_TOOL_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg(game_cfg_path: str, team_cfg_path: str) -> Tuple[Dict, Dict]:
    return load_json(game_cfg_path), load_json(team_cfg_path)


def _load_owned_items(lots_dir: str, owned_lot_ids: List[str]) -> List[ObjectItem]:
    items: List[ObjectItem] = []
    for lid in owned_lot_ids:
        path = os.path.join(lots_dir, f"{lid}.json")
        if not os.path.exists(path):
            continue
        lot = load_lot(path)
        items.extend(lot.items)
    return items


def cmd_eval(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    game_cfg, team_cfg = _load_cfg(args.game_cfg, args.team_cfg)
    forecasts = load_forecasts(args.forecasts_dir) if args.forecasts_dir else {}

    lot = load_lot(args.lot)
    owned_items = _load_owned_items(args.lots_dir, state.owned_lots)

    d_base, d_worst, d_best = marginal_value(state, owned_items, lot, forecasts, game_cfg)

    r_base, _ = summarize_delta(d_base)
    r_worst, _ = summarize_delta(d_worst)
    r_best, _ = summarize_delta(d_best)

    print(fmt_delta("BASE", d_base, r_base))
    print()
    print(fmt_delta("WORST", d_worst, r_worst))
    print()
    print(fmt_delta("BEST", d_best, r_best))

    pwin = args.pwin if args.pwin is not None else float(team_cfg.get("pwin_default", state.assumptions.pwin_default))
    v = float(d_base.delta_total)
    bmin, bmax = recommended_bid_range(v, pwin, safety=0.80)
    print()
    print(f"Suggest bid range (pwin={pwin:.2f}, v≈{v:.1f}): {bmin:.1f} .. {bmax:.1f}")


def cmd_rank(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    game_cfg, _team_cfg = _load_cfg(args.game_cfg, args.team_cfg)
    forecasts = load_forecasts(args.forecasts_dir) if args.forecasts_dir else {}

    owned_items = _load_owned_items(args.lots_dir, state.owned_lots)

    cand_paths = sorted(glob.glob(os.path.join(args.lots_dir, "*.json")))
    results = []
    for path in cand_paths:
        lot = load_lot(path)
        d_base, d_worst, _ = marginal_value(state, owned_items, lot, forecasts, game_cfg)
        results.append((d_base.delta_total, d_worst.delta_total, lot.lot_id, lot.title, d_base.flags))

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)

    print("lot_id | Δbase | Δworst | flags | title")
    for db, dw, lid, title, flags in results[: args.top]:
        fl = ",".join(flags[:2]) if flags else ""
        print(f"{lid:5s} | {db:+7.1f} | {dw:+7.1f} | {fl:20.20s} | {title}")


def cmd_suggest_bid(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    game_cfg, team_cfg = _load_cfg(args.game_cfg, args.team_cfg)
    forecasts = load_forecasts(args.forecasts_dir) if args.forecasts_dir else {}

    lot = load_lot(args.lot)
    owned_items = _load_owned_items(args.lots_dir, state.owned_lots)
    d_base, _d_worst, _d_best = marginal_value(state, owned_items, lot, forecasts, game_cfg)

    v = float(d_base.delta_total)
    pwin = float(args.pwin if args.pwin is not None else team_cfg.get("pwin_default", state.assumptions.pwin_default))
    safety = float(args.safety)

    bmin, bmax = recommended_bid_range(v, pwin, safety=safety)
    for bid in (bmin, (bmin + bmax) / 2.0, bmax):
        evr = ev_allpay(v, pwin, bid)
        print(f"bid={bid:7.1f} => EV={evr.ev:+.1f} (bid_max_nonneg={evr.bid_max_ev_nonneg:.1f})")

    limit = float(game_cfg.get("auction", {}).get("allpay_limit", 9999))
    spent = float(state.budget.allpay_spent or 0.0)
    if spent + bmax > limit:
        print(f"WARNING: all-pay limit risk: spent={spent:.1f} + bmax={bmax:.1f} > {limit:.1f}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lottool", description="Lot Valuation Tool for IЭС (NTO)")
    p.add_argument("--game-cfg", default=str(LOT_TOOL_ROOT / "config" / "config_game.json"))
    p.add_argument("--team-cfg", default=str(LOT_TOOL_ROOT / "config" / "config_team.json"))
    p.add_argument("--forecasts-dir", default=str(LOT_TOOL_ROOT / "data" / "forecasts"))

    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("eval", help="evaluate one lot")
    pe.add_argument("--state", default=str(LOT_TOOL_ROOT / "data" / "state.json"))
    pe.add_argument("--lot", required=True)
    pe.add_argument("--lots-dir", default=str(LOT_TOOL_ROOT / "data" / "lots"))
    pe.add_argument("--pwin", type=float, default=None)
    pe.set_defaults(func=cmd_eval)

    pr = sub.add_parser("rank", help="rank lots in lots-dir")
    pr.add_argument("--state", default=str(LOT_TOOL_ROOT / "data" / "state.json"))
    pr.add_argument("--lots-dir", default=str(LOT_TOOL_ROOT / "data" / "lots"))
    pr.add_argument("--top", type=int, default=15)
    pr.set_defaults(func=cmd_rank)

    ps = sub.add_parser("suggest-bid", help="suggest bid for a lot (all-pay EV)")
    ps.add_argument("--state", default=str(LOT_TOOL_ROOT / "data" / "state.json"))
    ps.add_argument("--lot", required=True)
    ps.add_argument("--lots-dir", default=str(LOT_TOOL_ROOT / "data" / "lots"))
    ps.add_argument("--pwin", type=float, default=None)
    ps.add_argument("--safety", type=float, default=0.80)
    ps.set_defaults(func=cmd_suggest_bid)

    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
