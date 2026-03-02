from __future__ import annotations

import csv
import os
from typing import Dict, Iterable, List, Optional, Tuple

ForecastSeries = Dict[str, Dict[int, float]]
ForecastPack = Dict[str, ForecastSeries]


def _nk(s: str) -> str:
    return "".join(ch.lower() for ch in str(s).strip())


def _af(x: str) -> Optional[float]:
    try:
        return float(str(x).strip().replace(",", "."))
    except Exception:
        return None


def _list_csv(folder: str) -> List[str]:
    if not os.path.isdir(folder):
        return []
    return sorted([os.path.join(folder, fn) for fn in os.listdir(folder) if fn.lower().endswith(".csv")])


def _kind(fn: str) -> str:
    s = os.path.basename(fn).lower()
    if "wind" in s or "вэс" in s:
        return "wind"
    if "solar" in s or "ses" in s or "сэс" in s:
        return "solar"
    if "load" in s or "consum" in s or "demand" in s or "нагруз" in s:
        return "load"
    return "unknown"


def _delim(sample: str) -> str:
    c = [",", ";", "\t", "|"]
    return max(c, key=lambda d: sample.count(d))


def _parse(fn: str) -> Tuple[List[str], List[List[str]]]:
    with open(fn, "r", encoding="utf-8") as f:
        sample = f.read(4096)
        d = _delim(sample)
        f.seek(0)
        r = csv.reader(f, delimiter=d)
        rows = [row for row in r if row and any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    return [_nk(h) for h in rows[0]], rows[1:]


def _is_long(header: List[str]) -> bool:
    h = set(header)
    return (any(k in h for k in ("t", "tick", "step", "time"))
            and any(k in h for k in ("id", "obj", "object", "name", "key", "type"))
            and any(k in h for k in ("v", "val", "value", "p", "power", "w", "wind", "solar", "load")))


def _col(header: List[str], *names: str) -> Optional[int]:
    for n in names:
        nn = _nk(n)
        if nn in header:
            return header.index(nn)
    return None


def _parse_long(header: List[str], data: List[List[str]]) -> ForecastSeries:
    it = _col(header, "tick", "t", "step", "time")
    ik = _col(header, "id", "obj", "object", "name", "key", "type")
    iv = _col(header, "value", "val", "v", "power", "p", "wind", "solar", "load")
    if it is None or ik is None or iv is None:
        return {}
    out: ForecastSeries = {}
    for row in data:
        if len(row) <= max(it, ik, iv):
            continue
        t = _af(row[it])
        v = _af(row[iv])
        if t is None or v is None:
            continue
        out.setdefault(str(row[ik]).strip(), {})[int(t)] = float(v)
    return out


def _parse_wide(header: List[str], data: List[List[str]]) -> ForecastSeries:
    if len(header) < 2:
        return {}
    keys = header[1:]
    out: ForecastSeries = {k: {} for k in keys}
    for row in data:
        if not row:
            continue
        t = _af(row[0])
        if t is None:
            continue
        tick = int(t)
        for j, k in enumerate(keys, start=1):
            if j >= len(row):
                continue
            v = _af(row[j])
            if v is None:
                continue
            out[k][tick] = float(v)
    return {k: tv for k, tv in out.items() if tv}


def load_forecasts(folder: str) -> ForecastPack:
    forecasts: ForecastPack = {}
    for fn in _list_csv(folder):
        kind = _kind(fn)
        header, data = _parse(fn)
        if not header or not data:
            continue
        series = _parse_long(header, data) if _is_long(header) else _parse_wide(header, data)
        if not series:
            continue
        forecasts.setdefault(kind, {})
        for key, tv in series.items():
            forecasts[kind].setdefault(key, {})
            forecasts[kind][key].update(tv)
    return forecasts


def lookup(forecasts: ForecastPack, kind: str, keys: Iterable[str], tick: int, default: float = 0.0) -> float:
    pack = forecasts.get(kind, {})
    for k in keys:
        if k in pack and tick in pack[k]:
            return float(pack[k][tick])
    return float(default)
