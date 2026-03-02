# utils.py
from __future__ import annotations

import math
from typing import Any, Optional

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

def safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default

def safe_path(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for key in path.split("."):
        if cur is None:
            return default
        cur = safe_getattr(cur, key, None)
    return default if cur is None else cur

def as_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace(",", ".")
        return float(s)
    except Exception:
        return default

def normalize_key(s: str) -> str:
    return "".join(ch.lower() for ch in str(s).strip())

def current_tick(psm: Any) -> int:
    for attr in ("tick", "t", "step"):
        v = safe_getattr(psm, attr, None)
        if isinstance(v, int):
            return v
    time_obj = safe_getattr(psm, "time", None)
    if time_obj is not None:
        now = safe_getattr(time_obj, "now", None)
        if isinstance(now, int):
            return now
    return 0

def is_nan(x: Optional[float]) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))
