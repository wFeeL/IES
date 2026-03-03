from __future__ import annotations

import unittest
from types import SimpleNamespace

from ies_bot_skeleton.online.compat import load_compat_profile, resolve_compat_profile


class _Orders2026:
    def lineOff(self, *args, **kwargs):
        return None

    def solarRobot(self, *args, **kwargs):
        return None


class CompatTests(unittest.TestCase):
    def test_load_explicit_profile(self) -> None:
        profile = load_compat_profile("2026")
        self.assertEqual(profile.season, "2026")
        self.assertIn("buy", profile.method_candidates)

    def test_auto_resolve_profile_by_signature(self) -> None:
        psm = SimpleNamespace(orders=_Orders2026())
        profile, auto = resolve_compat_profile(psm, season=None, logger=None)
        self.assertTrue(auto)
        self.assertEqual(profile.season, "2026")


if __name__ == "__main__":
    unittest.main()
