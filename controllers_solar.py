# controllers_solar.py
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, List

from adaptive import GameConst
from adapters import OrdersAdapter
from constants import SOLAR_EPSILON, SOLAR_EXPLORE_ANGLES
from forecasts import ForecastPack, lookup_forecast
from state import CalibState
from utils import as_float, clamp, current_tick, safe_path
from models import obj_id, obj_type

@dataclass
class SolarCmd:
    solar_id: str
    angle: int
    reason: str

def choose_angle(cur: int, desired: int, gc: GameConst) -> int:
    desired = int(clamp(desired, gc.solar_angle_min, gc.solar_angle_max))
    delta = desired - cur
    if abs(delta) <= gc.solar_max_step:
        return desired
    return cur + (gc.solar_max_step if delta > 0 else -gc.solar_max_step)

def update_solar_learning(psm: Any, st: CalibState, gc: GameConst) -> None:
    t = current_tick(psm)
    tmod = str(t % gc.period_ticks)
    for o in psm.objects:
        if obj_type(o) not in ("solarrobot", "solar"):
            continue
        sid = obj_id(o)
        cur_angle = int(as_float(safe_path(o, "angle.now.current", 0), 0.0))
        p_obs = as_float(safe_path(o, "power.now.generated", 0.0), 0.0)

        st.solar.best_angle.setdefault(sid, {})
        st.solar.best_power.setdefault(sid, {})

        best_p = st.solar.best_power[sid].get(tmod, -1.0)
        if p_obs > best_p:
            st.solar.best_power[sid][tmod] = p_obs
            st.solar.best_angle[sid][tmod] = cur_angle

def desired_angle(sid: str, t1: int, forecasts: ForecastPack, st: CalibState, gc: GameConst) -> int:
    ang = lookup_forecast(forecasts, "solar_best_angle", (sid, "solar_best_angle"), t1, default=None)
    if ang is not None:
        return int(clamp(ang, gc.solar_angle_min, gc.solar_angle_max))

    tmod = str(t1 % gc.period_ticks)
    learned = st.solar.best_angle.get(sid, {}).get(tmod, None)
    if learned is not None:
        if random.random() < SOLAR_EPSILON:
            return random.choice(SOLAR_EXPLORE_ANGLES)
        return int(learned)

    cycle = [0, 20, 40, 60, 80, 100, 124, 104, 84, 64, 44, 24]
    return cycle[(t1 % gc.period_ticks) % len(cycle)]

def solar_controller(psm: Any, forecasts: ForecastPack, st: CalibState, gc: GameConst) -> List[SolarCmd]:
    t1 = current_tick(psm) + 1
    cmds: List[SolarCmd] = []
    for o in psm.objects:
        if obj_type(o) not in ("solarrobot", "solar"):
            continue
        sid = obj_id(o)
        cur = int(as_float(safe_path(o, "angle.now.current", 0), 0.0))
        des = desired_angle(sid, t1, forecasts, st, gc)
        ang = choose_angle(cur, des, gc)
        cmds.append(SolarCmd(solar_id=sid, angle=ang, reason=f"desired={des}"))
        st.last_solar_angle[sid] = ang
    return cmds

def apply_solar(adapter: OrdersAdapter, cmds: List[SolarCmd]) -> None:
    for c in cmds:
        adapter.robot(c.solar_id, c.angle)
