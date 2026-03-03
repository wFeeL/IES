from __future__ import annotations

import unittest
from types import SimpleNamespace

from ies_bot_skeleton.online.adapters import OrdersAdapter
from ies_bot_skeleton.online.capabilities import discover_capabilities
from ies_bot_skeleton.online.compat import load_compat_profile


class _OrdersOK:
    def line_off(self, sub_id, line_no):
        return None

    def robot(self, obj_id, angle):
        return None

    def buy(self, power, price):
        return None

    def sell(self, power, price):
        return None

    def tps(self, obj_id, fuel):
        return None

    def storage(self, obj_id, power):
        return None


class _OrdersBadSig(_OrdersOK):
    def buy(self):  # wrong signature on purpose
        return None


class CapabilitiesTests(unittest.TestCase):
    def test_discover_capabilities_detects_methods(self) -> None:
        psm = SimpleNamespace(orders=_OrdersOK())
        profile = load_compat_profile("default")
        caps = discover_capabilities(psm, profile)
        self.assertTrue(caps.is_supported("buy"))
        self.assertTrue(caps.is_supported("sell"))
        self.assertTrue(caps.is_supported("tps"))
        self.assertTrue(caps.is_supported("line_off"))
        self.assertTrue(caps.is_supported("robot"))

    def test_adapter_returns_error_result_on_signature_mismatch(self) -> None:
        psm = SimpleNamespace(orders=_OrdersBadSig())
        profile = load_compat_profile("default")
        caps = discover_capabilities(psm, profile)
        adapter = OrdersAdapter(psm, compat_profile=profile, capabilities=caps, strict=False, dry_run=False)
        result = adapter.buy(5.0, 2.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "SIGNATURE_MISMATCH")

    def test_strict_mode_fails_fast_for_missing_critical_methods(self) -> None:
        psm = SimpleNamespace(orders=SimpleNamespace())
        profile = load_compat_profile("default")
        caps = discover_capabilities(psm, profile)
        with self.assertRaises(RuntimeError):
            OrdersAdapter(psm, compat_profile=profile, capabilities=caps, strict=True, dry_run=False)


if __name__ == "__main__":
    unittest.main()
