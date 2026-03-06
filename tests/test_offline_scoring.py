from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ies_bot_skeleton.offline.lottool import ensure_lottool_path

ensure_lottool_path()

from lottool.cli import _collect_owned_items
from lottool.model.types import Assumptions, Branch, Game, Lot, NetworkPlan, ObjectItem, State
from lottool.scoring.marginal import marginal_value
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
    def test_scoring_income_zero_if_unserved(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        objects = [ObjectItem(kind="houseA", id="H1", tariff_rub_per_mw_tick=100.0)]
        forecasts = {"load": {"H1": {0: 10.0}}}
        cfg = _base_cfg()
        cfg["market"]["market_max_power"] = 0
        cfg["market"]["instant_buy_max_power"] = 0

        out = score_state(state, objects, forecasts, cfg, "base")
        self.assertEqual(out.income, 0.0)
        self.assertGreater(out.penalties, 0.0)

    def test_scoring_qty_scaling(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        forecasts = {"wind": {"W1": {0: 5.0}}}
        cfg = _base_cfg()

        out_qty1 = score_state(
            state,
            objects=[ObjectItem(kind="wind", id="W1", qty=1)],
            forecasts=forecasts,
            cfg=cfg,
            scenario="base",
        )
        out_qty2 = score_state(
            state,
            objects=[ObjectItem(kind="wind", id="W1", qty=2)],
            forecasts=forecasts,
            cfg=cfg,
            scenario="base",
        )
        self.assertAlmostEqual(out_qty2.eco_points, out_qty1.eco_points * 2.0, places=6)

    def test_scoring_outage_not_increasing_demand(self) -> None:
        state = State(
            game=Game(ticks_per_day=48, horizon_ticks=2),
            network_plan=NetworkPlan(
                mode="branches",
                branches=[Branch(name="LOAD", role="load", objects=["H1"], soft_flow_limit_mw=30)],
            ),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
        )
        objects = [ObjectItem(kind="houseA", id="H1", tariff_rub_per_mw_tick=0.0)]
        forecasts = {"load": {"H1": {0: 10.0, 1: 10.0}}}
        cfg = _base_cfg()
        cfg["network"]["wear_threshold_mw"] = 0.0
        cfg["network"]["wear_limit"] = 0.0
        cfg["network"]["wear_k"] = 1.0
        cfg["network"]["outage_ticks"] = 1
        cfg["network"]["outage_loss_scale"] = 1.0
        cfg["market"]["market_max_power"] = 100.0
        cfg["market"]["instant_buy_max_power"] = 0.0

        out = score_state(state, objects, forecasts, cfg, "base")
        self.assertAlmostEqual(out.market_net, 100.0, places=6)
        self.assertTrue(any(note.startswith("UNSERVABLE_LOAD:") for note in out.notes))

    def test_owned_objects_override_affects_score(self) -> None:
        base_state = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1}),
            owned_objects_override=[ObjectItem(kind="wind", id="WOV", qty=1)],
        )
        no_override_state = State(
            game=base_state.game,
            network_plan=base_state.network_plan,
            assumptions=base_state.assumptions,
            owned_objects_override=[],
        )
        lot = Lot(lot_id="L1", title="load lot", items=[ObjectItem(kind="houseA", id="H1", qty=1)])
        forecasts = {
            "wind": {"WOV": {0: 5.0}},
            "load": {"H1": {0: 10.0}},
        }
        cfg = _base_cfg()
        cfg["market"]["market_max_power"] = 0.0
        cfg["market"]["instant_buy_max_power"] = 0.0

        with_override, _, _ = marginal_value(
            base_state,
            list(base_state.owned_objects_override),
            lot,
            forecasts,
            cfg,
        )
        without_override, _, _ = marginal_value(no_override_state, [], lot, forecasts, cfg)
        self.assertNotEqual(with_override.delta_total, without_override.delta_total)

    def test_collect_owned_items_includes_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lots_dir = Path(tmp)
            (lots_dir / "L1.json").write_text(
                json.dumps(
                    {
                        "lot_id": "L1",
                        "items": [{"kind": "wind", "id": "WLOT", "qty": 1}],
                    }
                ),
                encoding="utf-8",
            )
            state = State(
                owned_lots=["L1"],
                owned_objects_override=[ObjectItem(kind="houseA", id="HOV", qty=1)],
            )
            items = _collect_owned_items(state, str(lots_dir))
            ids = sorted(item.id for item in items)
            self.assertEqual(ids, ["HOV", "WLOT"])

    def test_storage_soc_init_fraction_changes_first_tick_behavior(self) -> None:
        state_empty = State(
            game=Game(ticks_per_day=48, horizon_ticks=1),
            network_plan=NetworkPlan(mode="branches", branches=[]),
            assumptions=Assumptions(
                corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1},
                storage_soc_init_fraction=0.0,
            ),
        )
        state_full = State(
            game=state_empty.game,
            network_plan=state_empty.network_plan,
            assumptions=Assumptions(
                corridor={"wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1},
                storage_soc_init_fraction=1.0,
            ),
        )
        objects = [
            ObjectItem(kind="storage", id="S1", qty=1),
            ObjectItem(kind="houseA", id="H1", qty=1, tariff_rub_per_mw_tick=50.0),
        ]
        forecasts = {"load": {"H1": {0: 5.0}}}
        cfg = _base_cfg()
        cfg["storage"]["capacity_mw_tick"] = 10.0
        cfg["storage"]["discharge_rate_mw"] = 10.0
        cfg["storage"]["charge_rate_mw"] = 0.0
        cfg["market"]["market_max_power"] = 0.0
        cfg["market"]["instant_buy_max_power"] = 0.0

        out_empty = score_state(state_empty, objects, forecasts, cfg, "base")
        out_full = score_state(state_full, objects, forecasts, cfg, "base")

        self.assertEqual(out_empty.income, 0.0)
        self.assertGreater(out_full.income, out_empty.income)

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
