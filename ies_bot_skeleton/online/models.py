# models.py
from __future__ import annotations

from typing import Any, Dict, List

from .forecasts import ForecastPack, lookup_forecast
from .utils import as_float, clamp, safe_getattr, safe_path, current_tick

def obj_id(o: Any) -> str:
    return str(safe_getattr(o, "id", ""))

def obj_type(o: Any) -> str:
    t = safe_getattr(o, "type", "")
    return str(t).lower()

def split_objects(psm: Any) -> Dict[str, List[Any]]:
    groups: Dict[str, List[Any]] = {"wind": [], "solar": [], "tps": [], "storage": [], "consumer": []}
    for o in psm.objects:
        t = obj_type(o)
        if t == "wind":
            groups["wind"].append(o)
        elif t in ("solarrobot", "solar"):
            groups["solar"].append(o)
        elif t == "tps":
            groups["tps"].append(o)
        elif t in ("storage", "accumulator", "battery", "accum", "energy_storage"):
            groups["storage"].append(o)
        elif t in ("house", "housea", "houseb", "office", "factory", "consumer"):
            groups["consumer"].append(o)
    return groups


def _avg_wind_now(psm: Any) -> float:
    wind_map = safe_getattr(psm, "wind", None)
    if not isinstance(wind_map, dict):
        return float("nan")
    vals = []
    for _, item in wind_map.items():
        cur = safe_getattr(item, "now", item)
        fv = as_float(cur, default=float("nan"))
        if fv == fv and fv >= 0:
            vals.append(fv)
    return float(sum(vals) / len(vals)) if vals else float("nan")

def calibrate_wind_k(psm: Any, forecasts: ForecastPack, wind_k: Dict[str, float]) -> None:
    t = current_tick(psm)
    for o in psm.objects:
        if obj_type(o) != "wind":
            continue
        wid = obj_id(o)
        w = lookup_forecast(forecasts, "wind", (wid, "wind"), t, default=None)
        if w is None:
            w_now = _avg_wind_now(psm)
            w = None if w_now != w_now else w_now
        if w is None or w <= 0 or w > 100.0:
            continue
        p_obs = as_float(safe_path(o, "power.now.generated", 0.0), 0.0)
        if p_obs <= 0:
            continue
        k = p_obs / (w ** 3)
        old = wind_k.get(wid, k)
        wind_k[wid] = 0.7 * old + 0.3 * k

def wind_power_estimate(wid: str, w: float, wind_k: Dict[str, float], p_cap: float) -> float:
    if w > 100.0:
        return clamp(w, 0.0, p_cap)
    k = wind_k.get(wid, 1.0)
    return clamp(k * (w ** 3), 0.0, p_cap)

def estimate_losses_next_tick(psm: Any, gen: float, load: float) -> float:
    losses_now = 0.0
    flow_now = 0.0
    nets = safe_getattr(psm, "networks", {}) or {}
    if isinstance(nets, dict):
        for _, net in nets.items():
            losses_now += as_float(safe_getattr(net, "losses", 0.0), 0.0)
            up = as_float(safe_getattr(net, "upflow", 0.0), 0.0)
            dn = as_float(safe_getattr(net, "downflow", 0.0), 0.0)
            flow_now += max(abs(up), abs(dn))
    if losses_now <= 0.0 or flow_now <= 0.0:
        return max(0.0, 0.02 * max(gen, load))
    ratio = max(gen, load) / flow_now if flow_now > 0 else 1.0
    return max(0.0, losses_now * (ratio ** 2))
