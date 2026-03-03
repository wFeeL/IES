# controllers_balance.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

from .adaptive import GameConst
from .adapters import OrdersAdapter
from .constants import (
    ENABLE_MARKET_BUY, ENABLE_MARKET_SELL,
    ENABLE_STORAGE,
    BUY_INSURE_FRACTION, SELL_FRACTION, SURPLUS_SELL_THRESHOLD,
    STORAGE_DISCHARGE_RESERVE_FRACTION, STORAGE_CHARGE_RESERVE_FRACTION,
    ENDGAME_FULL_DISCHARGE_TICKS, ENDGAME_SOFT_DISCHARGE_TICKS, ENDGAME_MEDIUM_DISCHARGE_TICKS,
    MARKET_URGENT_DEFICIT_THRESHOLD,
    TPS_ETA_NOMINAL,
)
from .forecasts import ForecastPack, lookup_forecast
from .models import obj_id, obj_type, split_objects, wind_power_estimate, estimate_losses_next_tick
from .state import CalibState
from .utils import as_float, clamp, current_tick, safe_getattr, safe_path

@dataclass
class BalanceForecast:
    gen: float
    load: float
    losses: float
    net: float

def forecast_balance_next_tick(psm: Any, forecasts: ForecastPack, st: CalibState, gc: GameConst) -> Tuple[BalanceForecast, BalanceForecast, BalanceForecast]:
    t1 = current_tick(psm) + 1

    def compute(scale: float) -> BalanceForecast:
        gen = 0.0
        load = 0.0
        for o in psm.objects:
            t = obj_type(o)
            oid = obj_id(o)

            if t == "wind":
                w = lookup_forecast(forecasts, "wind", (oid, "wind"), t1, default=None)
                p_cap = as_float(safe_getattr(o, "powerMax", 20.0), 20.0)
                if w is None:
                    gen += max(0.0, as_float(safe_path(o, "power.now.generated", 0.0), 0.0))
                else:
                    gen += wind_power_estimate(oid, max(0.0, w * scale), st.wind_k, p_cap)

            elif t in ("solarrobot", "solar"):
                s = lookup_forecast(forecasts, "solar", (oid, "solar", "solarrobot"), t1, default=None)
                p_cap = as_float(safe_getattr(o, "powerMax", 25.0), 25.0)
                if s is None:
                    gen += max(0.0, as_float(safe_path(o, "power.now.generated", 0.0), 0.0))
                else:
                    if s <= 1.5:
                        gen += clamp(s * p_cap * scale, 0.0, p_cap)
                    else:
                        gen += clamp(s * scale, 0.0, p_cap)

            elif t == "tps":
                gen += max(0.0, as_float(safe_path(o, "power.now.generated", 0.0), 0.0))

            elif t in ("house", "housea", "houseb", "office", "factory", "consumer"):
                c = lookup_forecast(forecasts, "load", (oid, t), t1, default=None)
                if c is None:
                    load += max(0.0, as_float(safe_path(o, "power.now.consumed", 0.0), 0.0))
                else:
                    load += max(0.0, c * scale)

        losses = estimate_losses_next_tick(psm, gen=gen, load=load)
        net = gen - load - losses
        return BalanceForecast(gen=gen, load=load, losses=losses, net=net)

    return compute(0.90), compute(1.00), compute(1.10)

def estimate_buy_price(gc: GameConst, urgent: bool = False) -> float:
    if urgent:
        return float(max(gc.external_buy_price, gc.instant_buy_price))
    return float(clamp((gc.external_buy_price + gc.external_sell_price) / 2.0, gc.external_sell_price, gc.external_buy_price))

def estimate_sell_price(gc: GameConst, urgent: bool = False) -> float:
    if urgent:
        return float(min(gc.external_sell_price, gc.instant_sell_price))
    return float(clamp((gc.external_buy_price + gc.external_sell_price) / 2.0, gc.external_sell_price, gc.external_buy_price))

def storage_available_discharge(storage_obj: Any, reserve_fraction: float = STORAGE_DISCHARGE_RESERVE_FRACTION) -> Tuple[float, float]:
    p_cap = as_float(safe_getattr(storage_obj, "powerMax", 20.0), 20.0)
    charge_now = as_float(safe_path(storage_obj, "charge.now", 0.0), 0.0)

    charge_max = as_float(safe_path(storage_obj, "charge.max", float("nan")), float("nan"))
    if charge_max != charge_max:
        charge_max = as_float(safe_getattr(storage_obj, "chargeMax", float("nan")), float("nan"))

    reserve = (reserve_fraction * charge_max) if (charge_max == charge_max and charge_max > 0) else (reserve_fraction * charge_now)
    return p_cap, max(0.0, charge_now - reserve)

def storage_available_charge(storage_obj: Any, reserve_fraction: float = STORAGE_CHARGE_RESERVE_FRACTION) -> Tuple[float, float]:
    p_cap = as_float(safe_getattr(storage_obj, "powerMax", 20.0), 20.0)
    charge_now = as_float(safe_path(storage_obj, "charge.now", 0.0), 0.0)

    charge_max = as_float(safe_path(storage_obj, "charge.max", float("nan")), float("nan"))
    if charge_max != charge_max:
        charge_max = as_float(safe_getattr(storage_obj, "chargeMax", float("nan")), float("nan"))

    if charge_max == charge_max and charge_max > 0:
        reserve_free = reserve_fraction * charge_max
        return p_cap, max(0.0, (charge_max - reserve_free) - charge_now)
    return p_cap, 0.0

def allocate_storage_for_deficit(
    storages: List[Any],
    deficit: float,
    adapter: OrdersAdapter,
    reserve_fraction: float = STORAGE_DISCHARGE_RESERVE_FRACTION,
) -> float:
    remaining = max(0.0, deficit)
    if not ENABLE_STORAGE:
        return remaining
    for s in storages:
        if remaining <= 1e-6:
            break
        sid = obj_id(s)
        p_cap, e_avail = storage_available_discharge(s, reserve_fraction=reserve_fraction)
        if e_avail <= 0:
            continue
        p = min(p_cap, e_avail, remaining)
        if p > 0 and adapter.set_storage_power(sid, +p):
            remaining -= p
    return remaining

def allocate_storage_for_surplus(
    storages: List[Any],
    surplus: float,
    adapter: OrdersAdapter,
    reserve_fraction: float = STORAGE_CHARGE_RESERVE_FRACTION,
) -> float:
    remaining = max(0.0, surplus)
    if not ENABLE_STORAGE:
        return remaining
    for s in storages:
        if remaining <= 1e-6:
            break
        sid = obj_id(s)
        p_cap, cap_left = storage_available_charge(s, reserve_fraction=reserve_fraction)
        if cap_left <= 0:
            continue
        p = min(p_cap, cap_left, remaining)
        if p > 0 and adapter.set_storage_power(sid, -p):
            remaining -= p
    return remaining

def allocate_tps_for_deficit(tps_list: List[Any], deficit: float, adapter: OrdersAdapter, gc: GameConst) -> float:
    remaining = max(0.0, deficit)
    if remaining <= 1e-6 or not tps_list:
        return remaining

    eta = max(0.05, float(TPS_ETA_NOMINAL))
    fuel_max = max(0.0, float(gc.tps_fuel_max))
    unit_power_cap = fuel_max * eta

    for tps in tps_list:
        if remaining <= 1e-6:
            break
        tid = obj_id(tps)
        desired_power = min(unit_power_cap, remaining)
        fuel = clamp(desired_power / eta, 0.0, fuel_max)
        if fuel > 0 and adapter.tps_fuel(tid, fuel):
            remaining = max(0.0, remaining - fuel * eta)
    return remaining

def apply_market(adapter: OrdersAdapter, deficit: float, surplus: float, gc: GameConst, urgent: bool = False) -> None:
    if ENABLE_MARKET_BUY and deficit > 1e-6:
        adapter.buy(min(gc.market_max_power, deficit), estimate_buy_price(gc, urgent=urgent))
    if ENABLE_MARKET_SELL and surplus > SURPLUS_SELL_THRESHOLD:
        adapter.sell(min(gc.market_max_power, SELL_FRACTION * surplus), estimate_sell_price(gc, urgent=urgent))


def _dynamic_discharge_reserve_fraction(psm: Any, gc: GameConst) -> float:
    base = float(STORAGE_DISCHARGE_RESERVE_FRACTION)
    game_len = int(as_float(safe_getattr(psm, "gameLength", gc.period_ticks), gc.period_ticks))
    rem = max(0, game_len - current_tick(psm) - 1)
    if rem <= ENDGAME_FULL_DISCHARGE_TICKS:
        return 0.0
    if rem <= ENDGAME_SOFT_DISCHARGE_TICKS:
        return min(base, 0.05)
    if rem <= ENDGAME_MEDIUM_DISCHARGE_TICKS:
        return min(base, 0.10)
    return base

def balance_controller(psm: Any, forecasts: ForecastPack, st: CalibState, gc: GameConst, adapter: OrdersAdapter) -> None:
    pess, base, optim = forecast_balance_next_tick(psm, forecasts, st, gc)
    deficit_wc = max(0.0, -pess.net)
    deficit_urgent = max(0.0, -base.net)

    groups = split_objects(psm)
    storages = groups["storage"]
    tps_list = groups["tps"]

    discharge_reserve = _dynamic_discharge_reserve_fraction(psm, gc)
    remaining = allocate_storage_for_deficit(storages, deficit_wc, adapter, reserve_fraction=discharge_reserve)
    remaining = allocate_tps_for_deficit(tps_list, remaining, adapter, gc)

    if ENABLE_MARKET_BUY:
        if deficit_urgent > MARKET_URGENT_DEFICIT_THRESHOLD:
            apply_market(adapter, deficit=max(remaining, deficit_urgent), surplus=0.0, gc=gc, urgent=True)
        elif deficit_wc > 1.0:
            apply_market(adapter, deficit=BUY_INSURE_FRACTION * deficit_wc, surplus=0.0, gc=gc)

    surplus = max(0.0, base.net)
    leftover = allocate_storage_for_surplus(storages, surplus, adapter)
    if ENABLE_MARKET_SELL and leftover > SURPLUS_SELL_THRESHOLD:
        apply_market(adapter, deficit=0.0, surplus=leftover, gc=gc, urgent=deficit_wc <= 0.1 and max(0.0, optim.net) > SURPLUS_SELL_THRESHOLD)
