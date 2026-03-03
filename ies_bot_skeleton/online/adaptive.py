# adaptive.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from .utils import as_float, normalize_key, safe_getattr

@dataclass
class GameConst:
    wear_limit: float = 14.0
    wear_preempt: float = 12.0
    overload_threshold: float = 40.0
    loss_tax: float = 2.0

    penalty_factory: float = 57.0
    penalty_house_office: float = 36.0
    free_offline_house_office: int = 5

    instant_buy_price: float = 20.0
    instant_sell_price: float = 0.0
    external_buy_price: float = 10.0
    external_sell_price: float = 1.0
    market_max_power: float = 60.0

    solar_angle_min: int = 0
    solar_angle_max: int = 124
    solar_max_step: int = 20
    period_ticks: int = 48

    tps_fuel_max: float = 20.0
    fuel_price: float = 0.5
    eco_tax_fuel: float = 1.5


def default_game_constants(
    period_ticks_fallback: int,
    wear_preempt_margin_fallback: float,
    tps_fuel_max_fallback: float,
) -> GameConst:
    gc = GameConst()
    gc.period_ticks = int(period_ticks_fallback)
    gc.tps_fuel_max = float(tps_fuel_max_fallback)
    gc.wear_preempt = max(0.0, gc.wear_limit - float(wear_preempt_margin_fallback))
    return gc

def _walk_find_number(obj: Any, key_substrings: Tuple[str, ...]) -> Optional[float]:
    seen = set()
    stack = [obj]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, dict):
            for k, v in cur.items():
                ks = normalize_key(k)
                if any(sub in ks for sub in key_substrings):
                    if isinstance(v, (int, float)):
                        return float(v)
                    fv = as_float(v, default=float("nan"))
                    if fv == fv:
                        return fv
                if isinstance(v, (dict, list, tuple)):
                    stack.append(v)
        elif isinstance(cur, (list, tuple)):
            for v in cur:
                if isinstance(v, (dict, list, tuple)):
                    stack.append(v)
    return None

def get_constants_from_engine(
    psm: Any,
    period_ticks_fallback: int,
    wear_preempt_margin_fallback: float,
    tps_fuel_max_fallback: float,
) -> GameConst:
    gc = default_game_constants(
        period_ticks_fallback=period_ticks_fallback,
        wear_preempt_margin_fallback=wear_preempt_margin_fallback,
        tps_fuel_max_fallback=tps_fuel_max_fallback,
    )

    candidates = []
    for name in ("config", "settings", "cfg", "game_config", "constants"):
        c = safe_getattr(psm, name, None)
        if c is not None:
            candidates.append(c)
    candidates.append(psm)

    for c in candidates:
        v = _walk_find_number(c, ("wearlimit", "faillimit", "wear_limit"))
        if v is not None:
            gc.wear_limit = v
            break

    for c in candidates:
        v = _walk_find_number(c, ("overloadthreshold", "overload_threshold"))
        if v is not None:
            gc.overload_threshold = v
            break

    pre = None
    for c in candidates:
        v = _walk_find_number(c, ("wearpreempt", "wear_preempt", "preempt"))
        if v is not None:
            pre = v
            break
    gc.wear_preempt = min(pre, gc.wear_limit) if pre is not None else max(0.0, gc.wear_limit - wear_preempt_margin_fallback)

    for c in candidates:
        v = _walk_find_number(c, ("losstax", "loss_tax", "taxloss", "lossprice"))
        if v is not None:
            gc.loss_tax = v
            break

    for c in candidates:
        v = _walk_find_number(c, ("instantbuy", "instant_buy"))
        if v is not None:
            gc.instant_buy_price = v
            break
    for c in candidates:
        v = _walk_find_number(c, ("instantsell", "instant_sell"))
        if v is not None:
            gc.instant_sell_price = v
            break
    for c in candidates:
        v = _walk_find_number(c, ("externalbuy", "exchangeexternalbuy", "external_buy"))
        if v is not None:
            gc.external_buy_price = v
            break
    for c in candidates:
        v = _walk_find_number(c, ("externalsell", "exchangeexternalsell", "external_sell"))
        if v is not None:
            gc.external_sell_price = v
            break
    for c in candidates:
        v = _walk_find_number(c, ("marketmaxpower", "exchangemaxpower", "market_max_power"))
        if v is not None:
            gc.market_max_power = v
            break

    for c in candidates:
        v = _walk_find_number(c, ("solaranglemin", "solar_angle_min", "robotminangle"))
        if v is not None:
            gc.solar_angle_min = int(v)
            break
    for c in candidates:
        v = _walk_find_number(c, ("solaranglemax", "solar_angle_max", "robotmaxangle"))
        if v is not None:
            gc.solar_angle_max = int(v)
            break
    for c in candidates:
        v = _walk_find_number(c, ("solarmaxstep", "solar_max_step", "robotmaxstep", "robotspeed"))
        if v is not None and v >= 1:
            gc.solar_max_step = int(v)
            break

    for c in candidates:
        v = _walk_find_number(c, ("fuelmax", "tpsfuelmax", "tps_fuel_max"))
        if v is not None:
            gc.tps_fuel_max = v
            break

    for c in candidates:
        v = _walk_find_number(c, ("fuelprice", "fuel_price"))
        if v is not None:
            gc.fuel_price = v
            break
    for c in candidates:
        v = _walk_find_number(c, ("ecotaxfuel", "eco_tax_fuel", "fuel_ecotax"))
        if v is not None:
            gc.eco_tax_fuel = v
            break

    for c in candidates:
        v = _walk_find_number(c, ("periodticks", "period_ticks", "dayticks", "ticksperday"))
        if v is not None and v >= 1:
            gc.period_ticks = int(v)
            break

    if gc.solar_angle_min > gc.solar_angle_max:
        gc.solar_angle_min, gc.solar_angle_max = gc.solar_angle_max, gc.solar_angle_min
    gc.solar_max_step = max(1, int(gc.solar_max_step))
    gc.wear_preempt = min(gc.wear_preempt, gc.wear_limit)

    return gc
