from __future__ import annotations

import unittest

from ies_bot_skeleton.offline.lottool import ensure_lottool_path

ensure_lottool_path()

from lottool.model.types import Assumptions, Branch, Game, NetworkPlan, ObjectItem, State
from lottool.scoring.score import score_state


def _base_cfg() -> dict:
    return {
        "fine": {"class3_pardon": 0, "class3_rub_per_mw": 36, "factory_rub_per_mw": 57},
        "eco": {
            "wind_points_per_mw_tick": 2,
            "solar_points_per_mw_tick": 3,
            "storage_discharge_points_per_mw_tick": 1,
            "eco_point_value_rub": 0.0,
        },
        "storage": {
            "capacity_mw_tick": 0,
            "charge_rate_mw": 0,
            "discharge_rate_mw": 0,
            "leak_fraction_per_tick": 0.0,
        },
        "network": {
            "mode": "branches",
            "soft_flow_mw": 30,
            "loss_alpha": 0.0,
            "wear_overload_mw": 40,
            "wear_risk_penalty_rub": 0.0,
            "loss_tax": 0.0,
            "wear_threshold_mw": 40,
            "wear_limit": 14,
            "wear_k": 0.2,
            "outage_ticks": 1,
            "outage_penalty_rub": 0.0,
            "outage_loss_scale": 1.0,
        },
        "market": {
            "external_buy_price": 10,
            "external_sell_price": 1,
            "instant_buy_price": 20,
            "instant_sell_price": 0,
            "market_max_power": 60,
            "instant_buy_max_power": 1_000_000,
            "instant_sell_max_power": 1_000_000,
        },
        "tps": {"fuel_max": 0, "eta_nominal": 0.4, "fuel_price": 0.5, "eco_tax_fuel": 1.5},
        "object_defaults": {"wind_k_default": 0.04, "wind_cap_mw": 20, "solar_cap_mw": 25},
    }


class OfflineScoringTests(unittest.TestCase):
    def test_penalty_is_applied_for_unserved_load(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        objects = [ObjectItem(kind="houseA", id="H1", tariff_rub_per_mw_tick=0.0)]
        forecasts = {"load": {"H1": {0: 10.0}}}
        cfg = _base_cfg()
        cfg["market"]["market_max_power"] = 0
        cfg["market"]["instant_buy_max_power"] = 0

        out = score_state(state, objects, forecasts, cfg, "base")
        self.assertGreater(out.penalties, 0.0)

    def test_instant_buy_is_used_when_market_limit_is_exceeded(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        objects = [ObjectItem(kind="houseA", id="H1", tariff_rub_per_mw_tick=0.0)]
        forecasts = {"load": {"H1": {0: 100.0}}}
        cfg = _base_cfg()
        cfg["market"]["market_max_power"] = 10
        cfg["market"]["instant_buy_max_power"] = 200

        out = score_state(state, objects, forecasts, cfg, "base")
        self.assertTrue(any(note.startswith("INSTANT_BUY:") for note in out.notes))
        self.assertGreater(out.market_net, 100.0 * cfg["market"]["external_buy_price"])

    def test_wear_outage_event_is_generated(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=3),
            network_plan=NetworkPlan(
                mode="branches",
                branches=[
                    Branch(name="GEN", role="gen", objects=["W1"], soft_flow_limit_mw=30),
                    Branch(name="LOAD", role="load", objects=["H1"], soft_flow_limit_mw=30),
                ],
            ),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        objects = [
            ObjectItem(kind="wind", id="W1", tariff_rub_per_mw_tick=0.0),
            ObjectItem(kind="houseA", id="H1", tariff_rub_per_mw_tick=0.0),
        ]
        forecasts = {
            "wind": {"W1": {0: 150.0, 1: 150.0, 2: 150.0}},
            "load": {"H1": {0: 50.0, 1: 50.0, 2: 50.0}},
        }
        cfg = _base_cfg()
        cfg["network"]["wear_threshold_mw"] = 40
        cfg["network"]["wear_limit"] = 1.0
        cfg["network"]["wear_k"] = 1.0
        cfg["network"]["outage_ticks"] = 1

        out = score_state(state, objects, forecasts, cfg, "base")
        self.assertTrue(any(note.startswith("WEAR_OUTAGE:") for note in out.notes))


if __name__ == "__main__":
    unittest.main()
