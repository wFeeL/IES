from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ies_bot_skeleton.online.adaptive import get_constants_from_engine
from ies_bot_skeleton.online.compat import load_compat_profile


class AdaptiveConstantsTests(unittest.TestCase):
    def test_prefers_path_priority_when_candidates_conflict(self) -> None:
        psm = SimpleNamespace(
            config={
                "low_priority": {"wearLimit": 11},
                "preferred": {"wearLimit": 19},
            },
            settings={"wearLimit": 14},
        )
        profile = load_compat_profile("default")
        profile.constants_paths["wear_limit"] = ["config.preferred", "settings"]

        gc = get_constants_from_engine(
            psm,
            period_ticks_fallback=48,
            wear_preempt_margin_fallback=2.0,
            tps_fuel_max_fallback=20.0,
            compat_profile=profile,
            override_path="/tmp/no-such-runtime.json",
        )
        self.assertEqual(gc.wear_limit, 19.0)
        self.assertIn("config.preferred", gc.constant_sources["wear_limit"].source_path)

    def test_runtime_override_applies(self) -> None:
        psm = SimpleNamespace(config={"wearLimit": 10})
        profile = load_compat_profile("default")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            path.write_text(
                json.dumps({"constants_override": {"wear_limit": 14, "period_ticks": 24}}),
                encoding="utf-8",
            )
            gc = get_constants_from_engine(
                psm,
                period_ticks_fallback=48,
                wear_preempt_margin_fallback=2.0,
                tps_fuel_max_fallback=20.0,
                compat_profile=profile,
                override_path=str(path),
            )
        self.assertEqual(gc.wear_limit, 14.0)
        self.assertEqual(gc.period_ticks, 24)
        self.assertEqual(gc.constant_sources["wear_limit"].source_path, f"override:{path}")


if __name__ == "__main__":
    unittest.main()
