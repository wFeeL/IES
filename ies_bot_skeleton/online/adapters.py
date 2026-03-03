# adapters.py
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from .utils import safe_getattr

@dataclass
class DetectedMethods:
    line_off: Optional[str]
    robot: Optional[str]
    buy: Optional[str]
    sell: Optional[str]
    tps: Optional[str]
    storage_signed: Optional[str]
    storage_charge: Optional[str]
    storage_discharge: Optional[str]

def _has_n_params(fn: Callable[..., Any], n: int) -> bool:
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        return len(params) in (n, n + 1)
    except Exception:
        return False

def _find_method(orders: Any, names: Tuple[str, ...], n_params: Optional[int] = None) -> Optional[str]:
    for name in names:
        fn = safe_getattr(orders, name, None)
        if callable(fn):
            if n_params is None or _has_n_params(fn, n_params):
                return name
    return None

class OrdersAdapter:
    """Авто-детект методов orders и безопасные вызовы.
    Convention for set_storage_power():
      power_mw > 0  => discharge (to grid)
      power_mw < 0  => charge
    """

    def __init__(self, psm: Any, storage_sign_convention: str = "auto") -> None:
        self.psm = psm
        self.orders = safe_getattr(psm, "orders", None)
        self.storage_sign_convention = storage_sign_convention

        self.detected = DetectedMethods(
            line_off=_find_method(self.orders, ("line_off", "lineOff", "line_off_by_id", "lineOffById"), n_params=2),
            robot=_find_method(self.orders, ("robot", "solar_robot", "ses", "solarRobot"), n_params=2),
            buy=_find_method(self.orders, ("buy", "exchange_buy", "market_buy"), n_params=2),
            sell=_find_method(self.orders, ("sell", "exchange_sell", "market_sell"), n_params=2),
            tps=_find_method(self.orders, ("tps", "tps_fuel", "thermo", "tpp", "tes"), n_params=2),
            storage_signed=None,
            storage_charge=None,
            storage_discharge=None,
        )

        self.detected.storage_signed = _find_method(
            self.orders,
            ("storage", "accumulator", "battery", "accum", "energy_storage", "es"),
            n_params=2,
        )
        self.detected.storage_charge = _find_method(
            self.orders,
            ("charge", "storage_charge", "accumulator_charge", "battery_charge"),
            n_params=2,
        )
        self.detected.storage_discharge = _find_method(
            self.orders,
            ("discharge", "storage_discharge", "accumulator_discharge", "battery_discharge"),
            n_params=2,
        )

        if self.detected.storage_charge and self.detected.storage_discharge:
            self.detected.storage_signed = None

    def _call(self, method_name: Optional[str], *args) -> bool:
        if not method_name:
            return False
        fn = safe_getattr(self.orders, method_name, None)
        if not callable(fn):
            return False
        try:
            fn(*args)
            return True
        except Exception:
            return False

    def line_off(self, sub_id: str, line_no: int) -> bool:
        return self._call(self.detected.line_off, sub_id, int(line_no))

    def robot(self, solar_id: str, angle: int) -> bool:
        return self._call(self.detected.robot, solar_id, int(angle))

    def buy(self, power: float, price: float) -> bool:
        return self._call(self.detected.buy, float(power), float(price))

    def sell(self, power: float, price: float) -> bool:
        return self._call(self.detected.sell, float(power), float(price))

    def tps_fuel(self, tps_id: str, fuel: float) -> bool:
        fuel = max(0.0, float(fuel))
        return self._call(self.detected.tps, tps_id, fuel)

    def set_storage_power(self, storage_id: str, power_mw: float) -> bool:
        power_mw = float(power_mw)
        if abs(power_mw) < 1e-9:
            return False

        if self.detected.storage_charge and self.detected.storage_discharge:
            if power_mw > 0:
                return self._call(self.detected.storage_discharge, storage_id, abs(power_mw))
            else:
                return self._call(self.detected.storage_charge, storage_id, abs(power_mw))

        m = self.detected.storage_signed
        if not m:
            return False

        conv = self.storage_sign_convention
        if conv == "auto":
            conv = "pos_charge" if "charge" in m.lower() else "pos_discharge"

        signed = power_mw if conv == "pos_discharge" else -power_mw if conv == "pos_charge" else power_mw
        return self._call(m, storage_id, signed)
