from __future__ import annotations

import csv
import copy
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ies_bot_skeleton.offline.lottool import ensure_lottool_path

from ..extensions import db
from ..models import Forecast, ForecastPeriod, GameSession

ensure_lottool_path()

from lottool.io.forecast_loader import load_forecasts as load_lottool_forecasts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORECASTS_DIR = ROOT / "lot_tool" / "data" / "forecasts"


@dataclass
class ForecastDiagnostics:
    errors: List[str]
    warnings: List[str]
    column_map: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "column_map": dict(self.column_map),
        }


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip())


def _empty_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    return {
        "wind": {},
        "solar": {},
        "load": {},
        "market": {},
    }


def _detect_delimiter(sample: str) -> str:
    options = [",", ";", "\t", "|"]
    return max(options, key=sample.count)


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None
    try:
        out = float(raw)
    except ValueError:
        return None
    return out


def _guess_columns(headers: List[str]) -> Dict[str, Any]:
    h = [_norm(x) for x in headers]
    out: Dict[str, Any] = {"consumption": {}}

    def find(*names: str) -> Optional[str]:
        for name in names:
            key = _norm(name)
            if key in h:
                return headers[h.index(key)]
        return None

    out["tick"] = find("tick", "t", "step", "time")
    out["wind"] = find("wind", "wind_speed", "ветер")
    out["illumination"] = find("illumination", "solar", "sun", "light", "освещенность")
    out["market_price"] = find("market_price", "price", "external_price", "рынок")

    for idx, raw in enumerate(headers):
        key = h[idx]
        if key in {
            _norm(out.get("tick", "")),
            _norm(out.get("wind", "")),
            _norm(out.get("illumination", "")),
            _norm(out.get("market_price", "")),
        }:
            continue
        if key.startswith("load_"):
            out["consumption"][raw] = raw
            continue
        if key.startswith("consumption_"):
            out["consumption"][raw] = raw
            continue
        if key in {"housea", "houseb", "office", "factory", "consumer"}:
            out["consumption"][raw] = raw

    if not out["consumption"]:
        for raw in headers:
            if _norm(raw) in {"load", "consumption", "demand"}:
                out["consumption"][raw] = raw

    return out


def _resolved_column_map(
    guessed: Dict[str, Any],
    incoming_map: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out = dict(guessed)
    out.setdefault("consumption", {})
    if incoming_map:
        for key in ("tick", "wind", "illumination", "market_price"):
            if incoming_map.get(key):
                out[key] = incoming_map[key]
        if isinstance(incoming_map.get("consumption"), dict):
            out["consumption"] = dict(incoming_map["consumption"])
    return out


def _read_csv(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    text = content.decode("utf-8", errors="replace")
    delim = _detect_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
    return headers, rows


@lru_cache(maxsize=1)
def _cached_bundled_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    if not DEFAULT_FORECASTS_DIR.exists():
        return _empty_forecast_pack()
    return load_lottool_forecasts(str(DEFAULT_FORECASTS_DIR))


def load_bundled_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    return copy.deepcopy(_cached_bundled_forecast_pack())


def is_forecast_pack_empty(pack: Dict[str, Dict[str, Dict[int, float]]] | None) -> bool:
    if not isinstance(pack, dict):
        return True
    for bucket in pack.values():
        if isinstance(bucket, dict) and bucket:
            return False
    return True


def bundled_forecast_summary() -> Dict[str, Any]:
    pack = load_bundled_forecast_pack()
    load_rows = pack.get("load", {}) or {}
    wind_rows = (pack.get("wind", {}) or {}).get("wind", {}) or {}
    solar_rows = (pack.get("solar", {}) or {}).get("solar", {}) or {}
    market_rows = (pack.get("market", {}) or {}).get("price", {}) or {}
    ticks = sorted(
        {
            *wind_rows.keys(),
            *solar_rows.keys(),
            *market_rows.keys(),
            *{tick for values in load_rows.values() for tick in values.keys()},
        }
    )

    def avg(values: Iterable[float]) -> Optional[float]:
        rows = [float(value) for value in values]
        if not rows:
            return None
        return round(sum(rows) / len(rows), 4)

    return {
        "mode": "builtin",
        "forecast_id": None,
        "name": "Встроенный базовый прогноз",
        "source_file": str(DEFAULT_FORECASTS_DIR),
        "count": len(ticks),
        "tick_from": ticks[0] if ticks else None,
        "tick_to": ticks[-1] if ticks else None,
        "avg_wind": avg(wind_rows.values()),
        "avg_illumination": avg(solar_rows.values()),
        "avg_market_price": avg(market_rows.values()),
        "load_series": sorted(load_rows.keys()),
        "warnings": [],
        "text": "Используется встроенный базовый прогноз проекта.",
    }


def parse_and_store_forecast(
    *,
    session_id: int,
    name: str,
    source_file: str,
    content: bytes,
    column_map: Optional[Dict[str, Any]] = None,
) -> Tuple[Forecast, ForecastDiagnostics]:
    session_obj = db.session.get(GameSession, session_id)
    if session_obj is None:
        raise ValueError(f"Session {session_id} not found")

    headers, rows = _read_csv(content)
    guessed = _guess_columns(headers)
    resolved = _resolved_column_map(guessed, column_map)

    errors: List[str] = []
    warnings: List[str] = []

    tick_col = resolved.get("tick")
    if not tick_col:
        errors.append("Не найден столбец tick/time")

    if not rows:
        errors.append("CSV пустой")

    periods: List[ForecastPeriod] = []
    for row_idx, row in enumerate(rows, start=2):
        if not tick_col:
            break
        tick_raw = row.get(tick_col)
        try:
            tick = int(float(str(tick_raw).strip().replace(",", ".")))
        except Exception:
            errors.append(f"Строка {row_idx}: некорректный tick '{tick_raw}'")
            continue

        wind = _parse_float(row.get(resolved.get("wind", ""))) if resolved.get("wind") else None
        illum = (
            _parse_float(row.get(resolved.get("illumination", "")))
            if resolved.get("illumination")
            else None
        )
        market_price = (
            _parse_float(row.get(resolved.get("market_price", "")))
            if resolved.get("market_price")
            else None
        )

        consumption: Dict[str, float] = {}
        cons_map = resolved.get("consumption", {}) or {}
        for metric_name, col_name in cons_map.items():
            value = _parse_float(row.get(col_name))
            if value is None:
                continue
            if value < 0:
                warnings.append(f"Строка {row_idx}: потребление {metric_name} < 0, обрезано до 0")
                value = 0.0
            consumption[_norm(metric_name)] = float(value)

        extra: Dict[str, Any] = {}
        known = {
            resolved.get("tick"),
            resolved.get("wind"),
            resolved.get("illumination"),
            resolved.get("market_price"),
        }
        known.update((resolved.get("consumption", {}) or {}).values())
        for key, value in row.items():
            if key in known:
                continue
            fv = _parse_float(value)
            extra[_norm(key)] = fv if fv is not None else value

        periods.append(
            ForecastPeriod(
                tick=tick,
                illumination=illum,
                wind=wind,
                market_price=market_price,
                consumption_json=consumption,
                extra_json=extra,
            )
        )

    if errors:
        raise ValueError("; ".join(errors))

    forecast = Forecast(
        session_id=session_id,
        name=name,
        source_file=source_file,
        column_map_json=resolved,
        metadata_json={
            "rows": len(periods),
            "warnings": len(warnings),
            "warnings_detail": list(warnings),
        },
    )
    forecast.periods = periods
    db.session.add(forecast)
    db.session.commit()

    return forecast, ForecastDiagnostics(errors=[], warnings=warnings, column_map=resolved)


def build_forecast_pack(forecast: Forecast) -> Dict[str, Dict[str, Dict[int, float]]]:
    wind: Dict[int, float] = {}
    solar: Dict[int, float] = {}
    market: Dict[int, float] = {}
    load_series: Dict[str, Dict[int, float]] = {}

    for period in forecast.periods:
        if period.wind is not None:
            wind[int(period.tick)] = float(period.wind)
        if period.illumination is not None:
            solar[int(period.tick)] = float(period.illumination)
        if period.market_price is not None:
            market[int(period.tick)] = float(period.market_price)
        for key, value in (period.consumption_json or {}).items():
            if value is None:
                continue
            load_series.setdefault(_norm(key), {})[int(period.tick)] = float(value)

    class3 = {}
    for tick in set().union(*[set(v.keys()) for v in load_series.values()] or [set()]):
        total = 0.0
        for key in ("housea", "houseb", "office", "consumer", "load"):
            total += load_series.get(key, {}).get(tick, 0.0)
        if total > 0:
            class3[tick] = total

    out = {
        "wind": {"wind": wind} if wind else {},
        "solar": {"solar": solar} if solar else {},
        "load": load_series,
        "market": {"price": market} if market else {},
    }
    if class3:
        out["load"]["class3"] = class3
    return out


def merge_uploaded_forecasts(
    forecasts: Iterable[Forecast],
) -> Dict[str, Dict[str, Dict[int, float]]]:
    merged: Dict[str, Dict[str, Dict[int, float]]] = {
        "wind": {},
        "solar": {},
        "load": {},
        "market": {},
    }
    for forecast in forecasts:
        pack = build_forecast_pack(forecast)
        for kind, bucket in pack.items():
            for series_key, values in bucket.items():
                merged[kind].setdefault(series_key, {}).update(values)
    return merged


def summarize_forecast(forecast: Forecast) -> Dict[str, Any]:
    periods = list(forecast.periods)
    if not periods:
        return {
            "forecast_id": forecast.id,
            "name": forecast.name,
            "source_file": forecast.source_file,
            "count": 0,
            "tick_from": None,
            "tick_to": None,
            "avg_wind": None,
            "avg_illumination": None,
            "avg_market_price": None,
            "load_series": [],
            "warnings": list((forecast.metadata_json or {}).get("warnings_detail") or []),
            "text": f"Прогноз '{forecast.name}' пустой.",
        }

    def avg(values: Iterable[Optional[float]]) -> Optional[float]:
        rows = [value for value in values if value is not None]
        if not rows:
            return None
        return round(sum(rows) / len(rows), 4)

    load_series = sorted(
        {
            key
            for period in periods
            for key in (period.consumption_json or {}).keys()
        }
    )
    return {
        "forecast_id": forecast.id,
        "name": forecast.name,
        "source_file": forecast.source_file,
        "count": len(periods),
        "tick_from": min(period.tick for period in periods),
        "tick_to": max(period.tick for period in periods),
        "avg_wind": avg(period.wind for period in periods),
        "avg_illumination": avg(period.illumination for period in periods),
        "avg_market_price": avg(period.market_price for period in periods),
        "load_series": load_series,
        "warnings": list((forecast.metadata_json or {}).get("warnings_detail") or []),
        "text": (
            f"Периоды {min(period.tick for period in periods)}-{max(period.tick for period in periods)}, "
            f"рядов потребления: {len(load_series)}."
        ),
    }
