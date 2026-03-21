from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from ies_bot_skeleton.web.services import strategy as strategy_service


def test_build_combo_catalog_limits_combination_growth(monkeypatch):
    available_lots = [
        SimpleNamespace(id=index, current_bid=10.0, name=f"Lot {index}") for index in range(1, 13)
    ]
    calls: list[tuple[int, ...]] = []

    def _fake_combo_eval(
        *,
        session,
        lots,
        strategy,
        forecast,
        singles_net_profit,
        standalone_bids,
        portfolio_lots=None,
        reserved_spend=0.0,
    ):
        del session, strategy, forecast, singles_net_profit, standalone_bids, portfolio_lots, reserved_spend
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
            recommended_bid_safe=float(8 * size),
            recommended_bid_balanced=float(10 * size),
            recommended_bid_aggressive=float(12 * size),
            cautious_bid=float(10 * size),
            target_bid=float(10 * size),
            hard_ceiling_bid=float(10 * size),
            budget_adjusted_bid=float(10 * size),
            working_bid=float(10 * size),
            working_bid_source="target",
            working_bid_reason="test",
            p_win=0.35,
            serious_competitors=3,
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


def test_build_combo_catalog_keeps_lot_when_working_bid_fits_budget(monkeypatch):
    lot = SimpleNamespace(id=1, current_bid=150.0, name="Expensive market lot")

    def _fake_combo_eval(
        *,
        session,
        lots,
        strategy,
        forecast,
        singles_net_profit,
        standalone_bids,
        portfolio_lots=None,
        reserved_spend=0.0,
    ):
        del session, strategy, forecast, singles_net_profit, standalone_bids, portfolio_lots, reserved_spend
        return strategy_service.ComboEvaluation(
            lot_ids=tuple(int(item.id) for item in lots),
            payload={},
            total_price=150.0,
            risk_adjusted_net_profit=42.0,
            utility_score=42.0,
            net_profit_base=42.0,
            risk_total=1.0,
            recommended_bid_safe=90.0,
            recommended_bid_balanced=100.0,
            recommended_bid_aggressive=120.0,
            cautious_bid=95.0,
            target_bid=120.0,
            hard_ceiling_bid=140.0,
            budget_adjusted_bid=100.0,
            working_bid=100.0,
            working_bid_source="budget_adjusted",
            working_bid_reason="budget-fit",
            p_win=0.4,
            serious_competitors=3,
            synergy_score=0.0,
            explanation="test",
            lot_bid_breakdown=[],
        )

    monkeypatch.setattr(strategy_service, "_combo_eval", _fake_combo_eval)

    rows = strategy_service._build_combo_catalog(
        session=SimpleNamespace(id=1),
        available_lots=[lot],
        strategy="balanced",
        forecast=None,
        remaining_budget=100.0,
        beam_width=3,
        max_group_size=3,
    )

    assert len(rows) == 1
    assert rows[0].lot_ids == (1,)
    assert rows[0].working_bid == pytest.approx(100.0)


def test_remaining_budget_subtracts_allpay_spend():
    session = SimpleNamespace(
        budget_total=200.0,
        allpay_spent=35.0,
        lots=[SimpleNamespace(status="bought", purchase_price=40.0)],
    )

    assert strategy_service._remaining_budget(session) == pytest.approx(125.0)


def test_strategy_weights_are_unified_by_default():
    from ies_bot_skeleton.web.services.ruleset import strategy_weights

    generation = strategy_weights({}, "generation")
    aggressive = strategy_weights({}, "aggressive")
    assert generation == aggressive


def test_best_pairs_for_lot_sorted_by_profit_desc():
    snapshot = {
        "best_pairs": [
            {
                "lot_ids": [1, 2],
                "net_profit_base": 14.0,
                "risk_adjusted_net_profit": 9.0,
                "utility_score": 5.0,
            },
            {
                "lot_ids": [1, 3],
                "net_profit_base": 22.0,
                "risk_adjusted_net_profit": 8.0,
                "utility_score": 4.0,
            },
            {
                "lot_ids": [1, 4],
                "net_profit_base": 18.0,
                "risk_adjusted_net_profit": 11.0,
                "utility_score": 6.0,
            },
        ]
    }

    rows = strategy_service.best_pairs_for_lot(strategy_snapshot=snapshot, lot_id=1, top_n=5)

    assert [float(row["net_profit_base"]) for row in rows] == [22.0, 18.0, 14.0]
