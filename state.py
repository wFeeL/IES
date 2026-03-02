# state.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Dict

STATE_FILE = "state.json"

@dataclass
class SolarLearn:
    best_angle: Dict[str, Dict[str, int]]
    best_power: Dict[str, Dict[str, float]]

@dataclass
class CalibState:
    wind_k: Dict[str, float]
    solar: SolarLearn
    last_solar_angle: Dict[str, int]
    printed_once: bool

def default_state() -> CalibState:
    return CalibState(
        wind_k={},
        solar=SolarLearn(best_angle={}, best_power={}),
        last_solar_angle={},
        printed_once=False,
    )

def load_state() -> CalibState:
    if not os.path.exists(STATE_FILE):
        return default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        solar = d.get("solar", {}) or {}
        return CalibState(
            wind_k=d.get("wind_k", {}) or {},
            solar=SolarLearn(
                best_angle=solar.get("best_angle", {}) or {},
                best_power=solar.get("best_power", {}) or {},
            ),
            last_solar_angle=d.get("last_solar_angle", {}) or {},
            printed_once=bool(d.get("printed_once", False)),
        )
    except Exception:
        return default_state()

def save_state(st: CalibState) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(st), f, ensure_ascii=False)
