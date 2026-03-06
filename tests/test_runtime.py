from __future__ import annotations

import unittest
from types import SimpleNamespace

from ies_bot_skeleton.online.adaptive import GameConst, default_game_constants
from ies_bot_skeleton.online.controllers_balance import forecast_balance_next_tick
from ies_bot_skeleton.online.forecasts import load_forecasts
from ies_bot_skeleton.online.models import split_objects
from ies_bot_skeleton.online.state import default_state


class RuntimeTests(unittest.TestCase):
    def test_split_objects_supports_houseA_and_houseB(self) -> None:
        psm = SimpleNamespace(
            objects=[
                SimpleNamespace(id="h1", type="houseA"),
                SimpleNamespace(id="h2", type="houseB"),
                SimpleNamespace(id="f1", type="factory"),
                SimpleNamespace(id="w1", type="wind"),
            ]
        )
        groups = split_objects(psm)
        self.assertEqual(len(groups["consumer"]), 3)
        self.assertEqual(len(groups["wind"]), 1)

    def test_default_constants_preempt_from_margin(self) -> None:
        gc = default_game_constants(
            period_ticks_fallback=48, wear_preempt_margin_fallback=2.5, tps_fuel_max_fallback=19
        )
        self.assertEqual(gc.period_ticks, 48)
        self.assertEqual(gc.tps_fuel_max, 19.0)
        self.assertAlmostEqual(gc.wear_preempt, gc.wear_limit - 2.5, places=6)

    def test_load_forecasts_from_psm_when_csv_absent(self) -> None:
        psm = SimpleNamespace(
            tick=10,
            forecasts=SimpleNamespace(
                houseA=[1.0, 2.0],
                houseB=[3.0, 4.0],
                factory=[5.0, 6.0],
                wind={"M1": [7.0, 8.0]},
                sunEast={"M1": [0.7, 0.8]},
                sunWest={"M1": [0.9, 1.0]},
            ),
        )
        fc = load_forecasts(psm)
        self.assertIn("load", fc)
        self.assertIn("wind", fc)
        self.assertIn("solar", fc)
        self.assertIn(11, fc["load"]["housea"])
        self.assertIn("wind", fc["wind"])
        self.assertIn("solar", fc["solar"])

    def test_balance_forecast_counts_housea_load(self) -> None:
        obj = SimpleNamespace(
            id="h1",
            type="houseA",
            power=SimpleNamespace(now=SimpleNamespace(consumed=4.0, generated=0.0)),
        )
        psm = SimpleNamespace(objects=[obj], networks={}, tick=0)
        st = default_state()
        gc = GameConst(period_ticks=48)
        forecasts = {"load": {"housea": {1: 10.0}}}

        pess, base, optim = forecast_balance_next_tick(psm, forecasts, st, gc)
        self.assertGreater(base.load, 0.0)
        self.assertLess(base.net, 0.0)
        self.assertGreaterEqual(pess.net, optim.net)


if __name__ == "__main__":
    unittest.main()
