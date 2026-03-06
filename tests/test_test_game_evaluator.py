from __future__ import annotations

import unittest
from pathlib import Path

from test_game.evaluator import evaluate_all_lots, evaluate_lot
from test_game.forecast_loader import load_forecast_csv
from test_game.game_data import DEFAULT_LOTS, START_SYSTEM, TOTAL_TICKS


class TestGameEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.forecast = load_forecast_csv(root / "test_game" / "data" / "forecast.csv")

    def test_evaluate_lot_uses_48_ticks(self) -> None:
        lot = {"items": {"wind": 1, "factory": 1}}
        out = evaluate_lot(
            current_system=START_SYSTEM,
            lot=lot,
            forecast=self.forecast,
            ticks=TOTAL_TICKS,
            simulations=50,
            seed=1,
        )
        self.assertGreater(out.profit_with_lot, out.base_profit)
        self.assertGreater(out.deterministic_lot_value, 0.0)
        self.assertGreaterEqual(out.max_bid, out.recommended_bid)

    def test_uncertainty_is_deterministic_with_seed(self) -> None:
        lot = {"items": {"tps": 1, "office": 1}}
        out1 = evaluate_lot(START_SYSTEM, lot, self.forecast, ticks=48, simulations=80, seed=42)
        out2 = evaluate_lot(START_SYSTEM, lot, self.forecast, ticks=48, simulations=80, seed=42)
        self.assertAlmostEqual(out1.expected_lot_value, out2.expected_lot_value, places=9)
        self.assertAlmostEqual(out1.p25_lot_value, out2.p25_lot_value, places=9)

    def test_evaluate_all_lots_returns_all_default_lots(self) -> None:
        out = evaluate_all_lots(
            current_system=START_SYSTEM,
            lots=DEFAULT_LOTS,
            forecast=self.forecast,
            ticks=48,
            simulations=10,
            seed=123,
        )
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()

