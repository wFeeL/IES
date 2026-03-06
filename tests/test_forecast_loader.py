from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ies_bot_skeleton.common.forecast_loader import ForecastLoadError, load_forecast_bundle


class ForecastLoaderTests(unittest.TestCase):
    def test_load_wide_and_long_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wind.csv").write_text("tick,M1\n0,3\n1,4\n", encoding="utf-8")
            (root / "load_long.csv").write_text(
                "tick,id,value\n0,houseA,10\n1,houseA,11\n", encoding="utf-8"
            )
            bundle = load_forecast_bundle(
                csv_dir=str(root), allow_psm_fallback=False, require_any=True
            )

            self.assertIn("m1", bundle.wind)
            self.assertIn("houseA", bundle.consumption_base)
            self.assertEqual(bundle.wind["m1"][0], 3.0)
            self.assertEqual(bundle.consumption_base["houseA"][1], 11.0)

    def test_nan_and_negative_values_are_filtered_or_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solar.csv").write_text("tick,S1\n0,-1\n1,NaN\n2,0.5\n", encoding="utf-8")
            bundle = load_forecast_bundle(
                csv_dir=str(root), allow_psm_fallback=False, require_any=True
            )
            self.assertEqual(bundle.solar["s1"][0], 0.0)
            self.assertNotIn(1, bundle.solar["s1"])
            self.assertEqual(bundle.solar["s1"][2], 0.5)

    def test_missing_series_raises_helpful_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ForecastLoadError):
                load_forecast_bundle(csv_dir=tmp, allow_psm_fallback=False, require_any=True)

    def test_missing_required_columns_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "load_long.csv").write_text("tick\n0\n1\n", encoding="utf-8")
            with self.assertRaises(ForecastLoadError):
                load_forecast_bundle(csv_dir=str(root), allow_psm_fallback=False, require_any=True)


if __name__ == "__main__":
    unittest.main()
