from __future__ import annotations

import json
from pathlib import Path

import pytest

from ies_bot_skeleton.domain.lot_analysis.network.approx import approx_network_cost
from ies_bot_skeleton.domain.lot_analysis.scoring.score import score_state
from ies_bot_skeleton.domain.lot_analysis.types import Branch, Game, NetworkPlan, ObjectItem, State

ROOT = Path(__file__).resolve().parents[1]
CONFIG_GAME_PATH = ROOT / "ies_bot_skeleton" / "resources" / "lot_analysis" / "config_game.json"


def _config_game() -> dict:
    return json.loads(CONFIG_GAME_PATH.read_text(encoding="utf-8"))


def test_network_approx_has_no_hidden_loss_or_risk_defaults():
    plan = NetworkPlan(
        branches=[Branch(name="GEN", role="gen", objects=["GEN1"], soft_flow_limit_mw=15.0)]
    )

    result = approx_network_cost(plan, {"GEN": 100.0}, {})

    assert result.loss_mw_tick == pytest.approx(0.0)
    assert result.loss_cost_rub == pytest.approx(0.0)
    assert result.risk_penalty_rub == pytest.approx(0.0)
    assert result.flags == []


def test_score_state_uses_fixed_tps_rules_without_forecast_factor():
    cfg = _config_game()
    state = State(
        game=Game(ticks_per_day=1, horizon_ticks=1),
        network_plan=NetworkPlan(
            branches=[
                Branch(name="GEN", role="gen", objects=["TPS1"], soft_flow_limit_mw=40.0),
                Branch(name="LOAD", role="load", objects=["HOUSE1"], soft_flow_limit_mw=40.0),
            ]
        ),
    )
    objects = [
        ObjectItem(kind="tps", id="TPS1", meta={"connection_point": "A"}),
        ObjectItem(
            kind="houseA",
            id="HOUSE1",
            tariff_rub_per_mw_tick=3.0,
            meta={"connection_point": "A"},
        ),
    ]
    forecasts = {
        "wind": {},
        "solar": {},
        "load": {"houseA": {0: 2.0}, "housea": {0: 2.0}, "class3": {0: 2.0}},
        "market": {"price": {0: 10.0}},
    }

    breakdown = score_state(state, objects, forecasts, cfg, "base")

    assert breakdown.penalties == pytest.approx(0.0)
    assert breakdown.network_losses_cost == pytest.approx(0.0)
    assert breakdown.risk_penalty == pytest.approx(0.0)
    assert breakdown.fuel_and_taxes == pytest.approx(1.0)
    assert breakdown.income == pytest.approx(6.0)
