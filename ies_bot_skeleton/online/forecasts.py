# forecasts.py
from __future__ import annotations

import csv
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .utils import as_float, current_tick, normalize_key, safe_getattr

ForecastSeries = Dict[str, Dict[int, float]]   # key -> tick -> value
ForecastPack = Dict[str, ForecastSeries]       # kind -> series

def _list_csv_files() -> List[str]:
    return sorted([fn for fn in os.listdir(".") if fn.lower().endswith(".csv") and os.path.isfile(fn)])

def _guess_kind_from_filename(fn: str) -> str:
    s = fn.lower()
    if "wind" in s or "вэс" in s:
        return "wind"
    if "solar" in s or "ses" in s or "сэс" in s:
        return "solar"
    if "load" in s or "consum" in s or "demand" in s or "нагруз" in s:
        return "load"
    if "angle" in s:
        return "solar_best_angle"
    return "unknown"

def _detect_delimiter(sample: str) -> str:
    candidates = [",", ";", "\t", "|"]
    return max(candidates, key=lambda d: sample.count(d))

def _parse_csv_generic(fn: str) -> Tuple[List[str], List[List[str]]]:
    with open(fn, "r", encoding="utf-8") as f:
        sample = f.read(4096)
        delim = _detect_delimiter(sample)
        f.seek(0)
        reader = csv.reader(f, delimiter=delim)
        rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        return [], []
    header = [normalize_key(h) for h in rows[0]]
    return header, rows[1:]

def _is_long_format(header: List[str]) -> bool:
    h = set(header)
    has_t = any(k in h for k in ("t", "tick", "step", "time"))
    has_id = any(k in h for k in ("id", "obj", "object", "name", "key", "type"))
    has_val = any(k in h for k in ("v", "val", "value", "p", "power", "w", "wind", "solar", "load", "angle"))
    return has_t and has_id and has_val

def _col_index(header: List[str], *names: str) -> Optional[int]:
    for n in names:
        nn = normalize_key(n)
        if nn in header:
            return header.index(nn)
    return None

def _parse_long(header: List[str], data: List[List[str]]) -> ForecastSeries:
    it = _col_index(header, "tick", "t", "step", "time")
    ik = _col_index(header, "id", "obj", "object", "name", "key", "type")
    iv = _col_index(header, "value", "val", "v", "power", "p", "wind", "solar", "load", "angle")
    if it is None or ik is None or iv is None:
        return {}

    series: ForecastSeries = {}
    for row in data:
        if len(row) <= max(it, ik, iv):
            continue
        t = as_float(row[it], default=float("nan"))
        if t != t:
            continue
        tick = int(t)
        key = str(row[ik]).strip()
        val = as_float(row[iv], default=float("nan"))
        if val != val:
            continue
        series.setdefault(key, {})[tick] = float(val)
    return series

def _parse_wide(header: List[str], data: List[List[str]]) -> ForecastSeries:
    if len(header) < 2:
        return {}
    keys = header[1:]
    series: ForecastSeries = {k: {} for k in keys}
    for row in data:
        if not row:
            continue
        t = as_float(row[0], default=float("nan"))
        if t != t:
            continue
        tick = int(t)
        for j, key in enumerate(keys, start=1):
            if j >= len(row):
                continue
            val = as_float(row[j], default=float("nan"))
            if val != val:
                continue
            series[key][tick] = float(val)
    return {k: v for k, v in series.items() if v}

def _extract_series(obj: Any, tick_start: int) -> Dict[int, float]:
    seq = obj
    if not isinstance(seq, (list, tuple)):
        then = safe_getattr(obj, "then", None)
        if isinstance(then, (list, tuple)):
            seq = then
    if not isinstance(seq, (list, tuple)):
        return {}

    out: Dict[int, float] = {}
    for i, v in enumerate(seq):
        fv = as_float(v, default=float("nan"))
        if fv == fv:
            out[tick_start + i] = float(fv)
    return out


def _avg_series(series_list: List[Dict[int, float]]) -> Dict[int, float]:
    if not series_list:
        return {}
    ticks = sorted({t for s in series_list for t in s.keys()})
    out: Dict[int, float] = {}
    for t in ticks:
        vals = [s[t] for s in series_list if t in s]
        if vals:
            out[t] = float(sum(vals) / len(vals))
    return out


def _load_from_psm(psm: Any) -> ForecastPack:
    forecasts: ForecastPack = {}
    fobj = safe_getattr(psm, "forecasts", None)
    if fobj is None:
        return forecasts

    t0 = current_tick(psm) + 1

    load_attrs = ("houseA", "houseB", "office", "factory", "consumer", "house", "load")
    for name in load_attrs:
        seq = safe_getattr(fobj, name, None)
        if seq is None:
            continue
        series = _extract_series(seq, t0)
        if not series:
            continue
        forecasts.setdefault("load", {})
        forecasts["load"][normalize_key(name)] = series

    wind_obj = safe_getattr(fobj, "wind", None)
    wind_series_pool: List[Dict[int, float]] = []
    if isinstance(wind_obj, dict):
        forecasts.setdefault("wind", {})
        for key, seq in wind_obj.items():
            series = _extract_series(seq, t0)
            if series:
                forecasts["wind"][str(key)] = series
                wind_series_pool.append(series)
    elif wind_obj is not None:
        series = _extract_series(wind_obj, t0)
        if series:
            forecasts.setdefault("wind", {})
            forecasts["wind"]["wind"] = series

    if wind_series_pool:
        forecasts.setdefault("wind", {})
        forecasts["wind"]["wind"] = _avg_series(wind_series_pool)

    solar_pool: List[Dict[int, float]] = []
    for attr in ("solar", "sunEast", "sunWest"):
        sobj = safe_getattr(fobj, attr, None)
        if isinstance(sobj, dict):
            forecasts.setdefault("solar", {})
            for key, seq in sobj.items():
                series = _extract_series(seq, t0)
                if series:
                    forecasts["solar"][str(key)] = series
                    solar_pool.append(series)
        elif sobj is not None:
            series = _extract_series(sobj, t0)
            if series:
                solar_pool.append(series)

    if solar_pool:
        forecasts.setdefault("solar", {})
        forecasts["solar"]["solar"] = _avg_series(solar_pool)

    return forecasts


def load_forecasts(psm: Any = None) -> ForecastPack:
    forecasts: ForecastPack = {}
    for fn in _list_csv_files():
        kind = _guess_kind_from_filename(fn)
        header, data = _parse_csv_generic(fn)
        if not header or not data:
            continue
        series = _parse_long(header, data) if _is_long_format(header) else _parse_wide(header, data)
        if not series:
            continue
        forecasts.setdefault(kind, {})
        for key, tv in series.items():
            forecasts[kind].setdefault(key, {})
            forecasts[kind][key].update(tv)
    if forecasts:
        return forecasts
    if psm is not None:
        return _load_from_psm(psm)
    return forecasts

def lookup_forecast(
    forecasts: ForecastPack,
    kind: str,
    key_candidates: Iterable[str],
    tick: int,
    default: Optional[float] = None,
) -> Optional[float]:
    pack = forecasts.get(kind, {})
    for key in key_candidates:
        if key in pack and tick in pack[key]:
            return float(pack[key][tick])
    nk = {normalize_key(k) for k in key_candidates}
    for key, tv in pack.items():
        if normalize_key(key) in nk and tick in tv:
            return float(tv[tick])
    return default
