from __future__ import annotations

import math
from types import SimpleNamespace

from ies_bot_skeleton.web.services import strategy as strategy_service


def test_build_combo_catalog_limits_combination_growth(monkeypatch):
    available_lots = [
        SimpleNamespace(id=index, current_bid=10.0, name=f"Lot {index}") for index in range(1, 13)
    ]
    calls: list[tuple[int, ...]] = []

    def _fake_combo_eval(*, session, lots, strategy, forecast, singles_net_profit, standalone_bids):
        lot_ids = tuple(int(lot.id) for lot in lots)
        calls.append(lot_ids)
        size = len(lot_ids)
        score = float(1000 - sum(lot_ids) - size)
        return strategy_service.ComboEvaluation(
            lot_ids=lot_ids,
            payload={},
            total_price=float(10 * size),
            risk_adjusted_net_profit=score,
            utility_score=score,
            net_profit_base=score,
            risk_total=float(size),
            cautious_bid=float(10 * size),
            target_bid=float(10 * size),
            hard_ceiling_bid=float(10 * size),
            budget_limited_bid=float(10 * size),
            synergy_score=0.0,
            explanation="test",
            lot_bid_breakdown=[],
        )

    monkeypatch.setattr(strategy_service, "_combo_eval", _fake_combo_eval)

    rows = strategy_service._build_combo_catalog(
        session=SimpleNamespace(id=1),
        available_lots=available_lots,
        strategy="balanced",
        forecast=None,
        remaining_budget=1000.0,
        beam_width=3,
        max_group_size=5,
    )

    assert rows
    assert {len(row.lot_ids) for row in rows} >= {1, 2, 3, 4, 5}

    exhaustive_total = sum(math.comb(len(available_lots), size) for size in range(1, 6))
    assert len(calls) < exhaustive_total / 4
