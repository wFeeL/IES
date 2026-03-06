from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _nk(value: str) -> str:
    return "".join(ch.lower() for ch in str(value).strip())


def _af(value: Any, default: float = float("nan")) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return default


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


@dataclass
class ForecastBundle:
    wind: Dict[str, Dict[int, float]] = field(default_factory=dict)
    solar: Dict[str, Dict[int, float]] = field(default_factory=dict)
    consumption_base: Dict[str, Dict[int, float]] = field(default_factory=dict)
    solar_best_angle: Dict[str, Dict[int, float]] = field(default_factory=dict)

    def to_pack(self) -> Dict[str, Dict[str, Dict[int, float]]]:
        return {
            "wind": dict(self.wind),
            "solar": dict(self.solar),
            "load": dict(self.consumption_base),
            "solar_best_angle": dict(self.solar_best_angle),
        }


class ForecastLoadError(RuntimeError):
    pass


def _detect_delimiter(sample: str) -> str:
    candidates = [",", ";", "\t", "|"]
    return max(candidates, key=lambda d: sample.count(d))


def _list_csv_files(folder: str) -> List[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(
        [
            os.path.join(folder, fn)
            for fn in os.listdir(folder)
            if fn.lower().endswith(".csv") and os.path.isfile(os.path.join(folder, fn))
        ]
    )


def _guess_kind(filename: str) -> str:
    s = os.path.basename(filename).lower()
    if "wind" in s or "вэс" in s:
        return "wind"
    if "solar" in s or "ses" in s or "сэс" in s:
        return "solar"
    if "angle" in s:
        return "solar_best_angle"
    if "load" in s or "consum" in s or "demand" in s or "нагруз" in s:
        return "consumption_base"
    if "house" in s or "factory" in s:
        return "consumption_base"
    return "unknown"


def _parse_csv(path: str) -> Tuple[List[str], List[List[str]]]:
    with open(path, "r", encoding="utf-8") as f:
        sample = f.read(4096)
        delim = _detect_delimiter(sample)
        f.seek(0)
        reader = csv.reader(f, delimiter=delim)
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    return [_nk(x) for x in rows[0]], rows[1:]


def _is_long(header: Sequence[str]) -> bool:
    h = set(header)
    has_t = any(x in h for x in ("t", "tick", "step", "time"))
    has_id = any(x in h for x in ("id", "obj", "object", "name", "key", "type"))
    has_v = any(
        x in h
        for x in ("v", "val", "value", "p", "power", "w", "wind", "solar", "load", "consumption")
    )
    return bool(has_t and has_id and has_v)


def _col(header: Sequence[str], *names: str) -> Optional[int]:
    for name in names:
        n = _nk(name)
        if n in header:
            return list(header).index(n)
    return None


def _sanitize_series(series: Dict[str, Dict[int, float]]) -> Dict[str, Dict[int, float]]:
    out: Dict[str, Dict[int, float]] = {}
    for key, tv in series.items():
        cleaned: Dict[int, float] = {}
        for tick, value in tv.items():
            fv = _af(value)
            if fv != fv:
                continue
            if fv < 0:
                fv = 0.0
            cleaned[int(tick)] = float(fv)
        if cleaned:
            out[str(key)] = cleaned
    return out


def _parse_long(
    header: Sequence[str], rows: Sequence[Sequence[str]]
) -> Dict[str, Dict[int, float]]:
    it = _col(header, "tick", "t", "step", "time")
    ik = _col(header, "id", "obj", "object", "name", "key", "type")
    iv = _col(
        header, "value", "val", "v", "power", "p", "w", "wind", "solar", "load", "consumption"
    )
    if it is None or ik is None or iv is None:
        return {}

    out: Dict[str, Dict[int, float]] = {}
    for row in rows:
        if len(row) <= max(it, ik, iv):
            continue
        tick = _af(row[it])
        val = _af(row[iv])
        if tick != tick or val != val:
            continue
        out.setdefault(str(row[ik]).strip(), {})[int(tick)] = float(val)
    return _sanitize_series(out)


def _parse_wide(
    header: Sequence[str], rows: Sequence[Sequence[str]]
) -> Dict[str, Dict[int, float]]:
    if len(header) < 2:
        return {}
    keys = [str(k) for k in header[1:]]
    out: Dict[str, Dict[int, float]] = {k: {} for k in keys}
    for row in rows:
        if not row:
            continue
        tick = _af(row[0])
        if tick != tick:
            continue
        for idx, key in enumerate(keys, start=1):
            if idx >= len(row):
                continue
            val = _af(row[idx])
            if val != val:
                continue
            out[key][int(tick)] = float(val)
    return _sanitize_series(out)


def _current_tick(psm: Any) -> int:
    for attr in ("tick", "t", "step"):
        val = _safe_getattr(psm, attr, None)
        if isinstance(val, int):
            return val
    time_obj = _safe_getattr(psm, "time", None)
    if time_obj is not None:
        now = _safe_getattr(time_obj, "now", None)
        if isinstance(now, int):
            return now
    return 0


def _extract_seq(obj: Any, tick_start: int) -> Dict[int, float]:
    seq = obj
    if not isinstance(seq, (list, tuple)):
        then = _safe_getattr(obj, "then", None)
        if isinstance(then, (list, tuple)):
            seq = then
    if not isinstance(seq, (list, tuple)):
        return {}
    out: Dict[int, float] = {}
    for i, v in enumerate(seq):
        fv = _af(v)
        if fv != fv:
            continue
        if fv < 0:
            fv = 0.0
        out[tick_start + i] = float(fv)
    return out


def _avg_series(series_list: Sequence[Dict[int, float]]) -> Dict[int, float]:
    ticks = sorted({t for series in series_list for t in series.keys()})
    out: Dict[int, float] = {}
    for tick in ticks:
        vals = [series[tick] for series in series_list if tick in series]
        if vals:
            out[tick] = float(sum(vals) / len(vals))
    return out


def load_bundle_from_csv(folder: str) -> ForecastBundle:
    bundle = ForecastBundle()
    for path in _list_csv_files(folder):
        header, rows = _parse_csv(path)
        if not header or not rows:
            continue
        kind = _guess_kind(path)
        parsed = _parse_long(header, rows) if _is_long(header) else _parse_wide(header, rows)
        if not parsed:
            continue
        target = None
        if kind == "wind":
            target = bundle.wind
        elif kind == "solar":
            target = bundle.solar
        elif kind == "consumption_base":
            target = bundle.consumption_base
        elif kind == "solar_best_angle":
            target = bundle.solar_best_angle
        if target is None:
            continue
        for key, tv in parsed.items():
            target.setdefault(key, {}).update(tv)
    return bundle


def load_bundle_from_psm(psm: Any) -> ForecastBundle:
    bundle = ForecastBundle()
    fobj = _safe_getattr(psm, "forecasts", None)
    if fobj is None:
        return bundle

    t0 = _current_tick(psm) + 1
    for name in ("houseA", "houseB", "office", "factory", "consumer", "house", "load"):
        seq = _safe_getattr(fobj, name, None)
        if seq is None:
            continue
        parsed = _extract_seq(seq, t0)
        if parsed:
            bundle.consumption_base[_nk(name)] = parsed

    wind_obj = _safe_getattr(fobj, "wind", None)
    wind_pool: List[Dict[int, float]] = []
    if isinstance(wind_obj, dict):
        for key, seq in wind_obj.items():
            parsed = _extract_seq(seq, t0)
            if parsed:
                bundle.wind[str(key)] = parsed
                wind_pool.append(parsed)
    elif wind_obj is not None:
        parsed = _extract_seq(wind_obj, t0)
        if parsed:
            bundle.wind["wind"] = parsed
            wind_pool.append(parsed)
    if wind_pool:
        bundle.wind["wind"] = _avg_series(wind_pool)

    solar_pool: List[Dict[int, float]] = []
    for attr in ("solar", "sunEast", "sunWest"):
        source = _safe_getattr(fobj, attr, None)
        if isinstance(source, dict):
            for key, seq in source.items():
                parsed = _extract_seq(seq, t0)
                if parsed:
                    bundle.solar[str(key)] = parsed
                    solar_pool.append(parsed)
        elif source is not None:
            parsed = _extract_seq(source, t0)
            if parsed:
                solar_pool.append(parsed)
    if solar_pool:
        bundle.solar["solar"] = _avg_series(solar_pool)

    return bundle


def validate_bundle(bundle: ForecastBundle, *, require_any: bool = False) -> None:
    has_any = bool(bundle.wind or bundle.solar or bundle.consumption_base)
    if require_any and not has_any:
        raise ForecastLoadError(
            "No forecasts found. Provide CSV files (wind/solar/load) or ensure psm.forecasts is available."
        )


def load_forecast_bundle(
    *,
    csv_dir: str = ".",
    psm: Any = None,
    allow_psm_fallback: bool = True,
    require_any: bool = False,
) -> ForecastBundle:
    bundle = load_bundle_from_csv(csv_dir)
    if (
        allow_psm_fallback
        and not (bundle.wind or bundle.solar or bundle.consumption_base)
        and psm is not None
    ):
        bundle = load_bundle_from_psm(psm)
    validate_bundle(bundle, require_any=require_any)
    return bundle


def lookup_pack_value(
    pack: Dict[str, Dict[str, Dict[int, float]]],
    kind: str,
    keys: Iterable[str],
    tick: int,
    default: Optional[float] = None,
) -> Optional[float]:
    series = pack.get(kind, {})
    for key in keys:
        if key in series and tick in series[key]:
            return float(series[key][tick])
    nk = {_nk(k) for k in keys}
    for key, tv in series.items():
        if _nk(key) in nk and tick in tv:
            return float(tv[tick])
    return default


__all__ = [
    "ForecastBundle",
    "ForecastLoadError",
    "load_bundle_from_csv",
    "load_bundle_from_psm",
    "load_forecast_bundle",
    "lookup_pack_value",
]
