from __future__ import annotations

import compileall
import unittest
from pathlib import Path

from ies_bot_skeleton.common.forecast_loader import lookup_pack_value


class QualityGateTests(unittest.TestCase):
    def test_compileall(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        ok = compileall.compile_dir(str(repo_root), quiet=1)
        self.assertTrue(ok)

    def test_forecast_loader_basic(self) -> None:
        pack = {"wind": {"W1": {0: 3.5, 1: 4.0}}}
        self.assertEqual(lookup_pack_value(pack, "wind", ("W1",), 0, default=0.0), 3.5)
        self.assertEqual(lookup_pack_value(pack, "wind", ("w1",), 1, default=0.0), 4.0)
        self.assertEqual(lookup_pack_value(pack, "wind", ("MISSING",), 1, default=7.0), 7.0)


if __name__ == "__main__":
    unittest.main()
