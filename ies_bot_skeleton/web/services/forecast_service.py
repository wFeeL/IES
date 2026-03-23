from __future__ import annotations

import csv
import copy
import io
import statistics
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ies_bot_skeleton.domain.lot_analysis.forecast_loader import load_forecasts

from ..extensions import db
from ..models import Forecast, ForecastPeriod, GameSession, ObjectType
from .formatting import format_tick_range
from .test_game_preset import TEST_GAME_BUNDLED_FORECAST_NAME
from .ui_text import FORECAST_SERVICE_LOAD_KEYS, forecast_series_label

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORECASTS_DIR = ROOT / "resources" / "lot_analysis" / "default_forecasts"

CANONICAL_FACTOR_KEYS = (
    "wind_factor",
    "solar_factor",
    "market_price_buy",
    "market_price_sell",
    "balancing_penalty_price",
    "fuel_price",
    "temperature",
    "time_of_day",
)
CANONICAL_PROFILE_KEYS = (
    "factory_load",
    "office_load",
    "house_a_load",
    "house_b_load",
    "hospital_load",
    "storage_default_profile",
)

CANONICAL_LOAD_KEYS = (
    "house_a",
    "house_b",
    "office",
    "factory",
    "hospital",
    "consumer",
    "load",
    "class3",
)
_CANONICAL_LOAD_SET = set(CANONICAL_LOAD_KEYS)
_FACTOR_SET = set(CANONICAL_FACTOR_KEYS)
_PROFILE_SET = set(CANONICAL_PROFILE_KEYS)

_FACTOR_ALIASES: Dict[str, tuple[str, ...]] = {
    "wind_factor": ("wind_factor", "wind", "wind_speed", "ветер"),
    "solar_factor": ("solar_factor", "solar", "illumination", "sun", "light", "освещенность"),
    "market_price_buy": (
        "market_price_buy",
        "market_price",
        "price_buy",
        "price",
        "external_price",
        "рынок",
    ),
    "market_price_sell": ("market_price_sell", "price_sell", "sell_price"),
    "balancing_penalty_price": ("balancing_penalty_price", "imbalance_penalty", "balancing_penalty"),
    "fuel_price": ("fuel_price", "fuel"),
    "temperature": ("temperature", "temp", "t_air"),
    "time_of_day": ("time_of_day", "hour", "tod"),
}

_PROFILE_ALIASES: Dict[str, tuple[str, ...]] = {
    "factory_load": (
        "factory_load",
        "factory",
        "load_factory",
        "consumption_factory",
        "demand_factory",
    ),
    "office_load": ("office_load", "office", "load_office", "consumption_office", "demand_office"),
    "house_a_load": (
        "house_a_load",
        "house_a",
        "housea",
        "load_house_a",
        "load_housea",
        "consumption_house_a",
        "consumption_housea",
    ),
    "house_b_load": (
        "house_b_load",
        "house_b",
        "houseb",
        "load_house_b",
        "load_houseb",
        "consumption_house_b",
        "consumption_houseb",
    ),
    "hospital_load": (
        "hospital_load",
        "hospital",
        "load_hospital",
        "consumption_hospital",
        "demand_hospital",
    ),
    "storage_default_profile": ("storage_default_profile", "storage_profile"),
}

_LEGACY_PROFILE_TO_LOAD = {
    "house_a_load": "house_a",
    "house_b_load": "house_b",
    "office_load": "office",
    "factory_load": "factory",
    "hospital_load": "hospital",
}

_LEGACY_LOAD_TO_PROFILE = {
    "house": "house_a_load",
    "housea": "house_a_load",
    "house_a": "house_a_load",
    "load_house": "house_a_load",
    "load_housea": "house_a_load",
    "load_house_a": "house_a_load",
    "consumption_house": "house_a_load",
    "consumption_house_a": "house_a_load",
    "houseb": "house_b_load",
    "house_b": "house_b_load",
    "load_houseb": "house_b_load",
    "load_house_b": "house_b_load",
    "consumption_houseb": "house_b_load",
    "consumption_house_b": "house_b_load",
    "office": "office_load",
    "load_office": "office_load",
    "consumption_office": "office_load",
    "factory": "factory_load",
    "load_factory": "factory_load",
    "consumption_factory": "factory_load",
    "hospital": "hospital_load",
    "load_hospital": "hospital_load",
    "consumption_hospital": "hospital_load",
}

WEATHER_RAW_ALIASES: Dict[str, tuple[str, ...]] = {
    "wind_from": ("wind_from", "windfrom", "wind_min", "wind_start", "wind_lower"),
    "wind_to": ("wind_to", "windto", "wind_max", "wind_end", "wind_upper"),
    "sun_east": ("sun_east", "suneast", "illumination_east", "light_east", "east_sun"),
    "sun_west": ("sun_west", "sunwest", "illumination_west", "light_west", "west_sun"),
    "hospital": ("hospital", "hospital_load", "load_hospital", "consumption_hospital"),
    "factory": ("factory", "factory_load", "load_factory", "consumption_factory"),
    "house_a": ("house_a", "housea", "load_house_a", "load_housea", "consumption_house_a"),
    "house_b": ("house_b", "houseb", "load_house_b", "load_houseb", "consumption_house_b"),
    "office": ("office", "office_load", "load_office", "consumption_office"),
}

WEATHER_CATEGORY_SERIES_ORDER = (
    "hospital",
    "factory",
    "house_a",
    "house_b",
    "office",
    "consumer",
)

WEATHER_SERIES_LABELS: Dict[str, str] = {
    "total_consumption": "Суммарное потребление",
    "total_generation": "Суммарная генерация",
    "balance": "Энергобаланс",
    "wind_avg": "Средний ветер",
    "wind_gen": "Генерация ветра",
    "solar_improved": "Солнечная генерация (improved)",
    "solar_simple": "Солнечная генерация (simple)",
    "solar_east_gen": "Солнечная генерация east",
    "solar_west_gen": "Солнечная генерация west",
    "sun_east": "Солнце east",
    "sun_west": "Солнце west",
}


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
    if out != out:
        return None
    return out


def _detect_delimiter(sample: str) -> str:
    options = [",", ";", "\t", "|"]
    return max(options, key=sample.count)


def _read_csv(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    text = content.decode("utf-8", errors="replace")
    delim = _detect_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
    return headers, rows


def _series_stats(values: Iterable[Optional[float]]) -> Optional[Dict[str, float]]:
    rows = [float(value) for value in values if value is not None]
    if not rows:
        return None
    return {
        "min": round(min(rows), 4),
        "max": round(max(rows), 4),
        "avg": round(sum(rows) / len(rows), 4),
        "median": round(float(statistics.median(rows)), 4),
    }


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _round_metric(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _series_has_values(series: Dict[int, float] | None) -> bool:
    return bool(series)


def _series_value(series: Dict[int, float] | None, tick: int) -> Optional[float]:
    if not isinstance(series, dict):
        return None
    value = series.get(int(tick))
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return float(parsed)


def _sum_available(*values: Optional[float]) -> Optional[float]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return float(sum(numeric))


def _mean(values: Iterable[float]) -> float:
    rows = [float(value) for value in values]
    if not rows:
        return 0.0
    return float(sum(rows) / len(rows))


def _series_payload(ticks: List[int], series: Dict[int, float] | None) -> List[Optional[float]]:
    if not series:
        return []
    out: List[Optional[float]] = []
    has_any = False
    for tick in ticks:
        value = _series_value(series, tick)
        if value is not None:
            has_any = True
        out.append(_round_metric(value, 4))
    return out if has_any else []


def _series_numbers(series: Iterable[Optional[float]]) -> List[float]:
    return [float(value) for value in series if value is not None]


def _series_mean(series: Iterable[Optional[float]]) -> Optional[float]:
    rows = _series_numbers(series)
    if not rows:
        return None
    return round(sum(rows) / len(rows), 4)


def _series_min(series: Iterable[Optional[float]]) -> Optional[float]:
    rows = _series_numbers(series)
    if not rows:
        return None
    return round(min(rows), 4)


def _series_max(series: Iterable[Optional[float]]) -> Optional[float]:
    rows = _series_numbers(series)
    if not rows:
        return None
    return round(max(rows), 4)


def _build_load_series_display(
    load_rows: Dict[str, Dict[int, float]],
    *,
    display_labels: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    display: List[Dict[str, Any]] = []
    labels = dict(display_labels or {})
    for key in sorted(load_rows.keys()):
        values = load_rows.get(key) or {}
        avg_value = _series_stats(values.values())
        label = str(labels.get(key) or "").strip() or forecast_series_label(key)
        display.append(
            {
                "key": key,
                "label": label,
                "avg": float(avg_value["avg"]) if avg_value is not None else None,
                "is_service": key in FORECAST_SERVICE_LOAD_KEYS,
                "is_raw_label": key in labels,
            }
        )
    return display


def _build_series_stats_display(
    series_stats: Dict[str, Dict[str, float] | None],
    load_rows: Dict[str, Dict[int, float]],
    *,
    display_labels: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    labels = dict(display_labels or {})
    for key in CANONICAL_FACTOR_KEYS:
        rows.append(
            {
                "key": key,
                "label": str(labels.get(key) or "").strip() or forecast_series_label(key),
                "group": "factor",
                "stats": dict(series_stats.get(key) or {}) or None,
                "is_raw_label": key in labels,
            }
        )
    for key in CANONICAL_PROFILE_KEYS:
        rows.append(
            {
                "key": key,
                "label": str(labels.get(key) or "").strip() or forecast_series_label(key),
                "group": "profile",
                "stats": dict(series_stats.get(key) or {}) or None,
                "is_raw_label": key in labels,
            }
        )
    for key in sorted(load_rows.keys()):
        rows.append(
            {
                "key": key,
                "label": str(labels.get(key) or "").strip() or forecast_series_label(key),
                "group": "load_service" if key in FORECAST_SERVICE_LOAD_KEYS else "load",
                "stats": _series_stats((load_rows.get(key) or {}).values()),
                "is_raw_label": key in labels,
            }
        )
    return rows


def _ordered_mapped_columns(
    *,
    headers: Iterable[str] | None,
    resolved_map: Dict[str, Any] | None,
) -> List[Dict[str, str]]:
    column_map = dict(resolved_map or {})
    mapped: List[Dict[str, str]] = []
    seen_raw: set[str] = set()

    for factor_key, source_column in dict(column_map.get("factors") or {}).items():
        canonical = _norm(str(factor_key))
        raw_name = str(source_column or "").strip()
        if canonical not in _FACTOR_SET or not raw_name:
            continue
        raw_norm = _norm(raw_name)
        if raw_norm in seen_raw:
            continue
        seen_raw.add(raw_norm)
        mapped.append(
            {
                "raw_name": raw_name,
                "canonical_key": canonical,
                "mapped_kind": "factor",
            }
        )

    for profile_key, source_column in dict(column_map.get("profiles") or {}).items():
        canonical_profile = _canonical_profile_key(str(profile_key))
        raw_name = str(source_column or "").strip()
        if canonical_profile is None or not raw_name:
            continue
        raw_norm = _norm(raw_name)
        if raw_norm in seen_raw:
            continue
        seen_raw.add(raw_norm)
        mapped.append(
            {
                "raw_name": raw_name,
                "canonical_key": canonical_profile,
                "mapped_kind": "profile",
            }
        )

    headers_list = [str(header) for header in (headers or []) if str(header).strip()]
    if not headers_list:
        return mapped
    order = {_norm(header): index for index, header in enumerate(headers_list)}
    return sorted(
        mapped,
        key=lambda row: (
            order.get(_norm(row["raw_name"]), len(order) + 10_000),
            row["raw_name"].lower(),
        ),
    )


def _build_mapped_raw_stats_display(
    *,
    mapped_columns: List[Dict[str, str]],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for column in mapped_columns:
        canonical_key = str(column.get("canonical_key") or "").strip()
        mapped_kind = str(column.get("mapped_kind") or "").strip()
        raw_name = str(column.get("raw_name") or "").strip()
        if not canonical_key or not raw_name:
            continue
        source_bucket = factors if mapped_kind == "factor" else profiles
        stats = _series_stats((source_bucket.get(canonical_key) or {}).values())
        rows.append(
            {
                "key": raw_name,
                "label": raw_name,
                "group": mapped_kind,
                "mapped_kind": mapped_kind,
                "canonical_key": canonical_key,
                "stats": stats,
                "is_raw_label": True,
            }
        )
    return rows


def _column_mapping_rows(mapped_columns: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for column in mapped_columns:
        canonical_key = str(column.get("canonical_key") or "").strip()
        raw_name = str(column.get("raw_name") or "").strip()
        mapped_kind = str(column.get("mapped_kind") or "").strip()
        if not canonical_key or not raw_name:
            continue
        rows.append(
            {
                "raw_name": raw_name,
                "canonical_key": canonical_key,
                "mapped_kind": mapped_kind,
                "interpreted_meaning": forecast_series_label(canonical_key),
            }
        )
    return rows


def _build_mapped_load_series_display(
    *,
    mapped_columns: List[Dict[str, str]],
    profiles: Dict[str, Dict[int, float]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for column in mapped_columns:
        mapped_kind = str(column.get("mapped_kind") or "").strip()
        canonical_key = str(column.get("canonical_key") or "").strip()
        raw_name = str(column.get("raw_name") or "").strip()
        if mapped_kind != "profile" or not canonical_key or not raw_name:
            continue
        legacy_load_key = _legacy_load_from_profile_key(canonical_key)
        if legacy_load_key is None:
            continue
        stats = _series_stats((profiles.get(canonical_key) or {}).values())
        rows.append(
            {
                "key": raw_name,
                "label": raw_name,
                "avg": float(stats["avg"]) if stats is not None else None,
                "is_service": False,
                "is_raw_label": True,
                "canonical_key": canonical_key,
                "load_key": legacy_load_key,
            }
        )
    return rows


def _canonical_load_key(raw_key: str) -> Optional[str]:
    key = _norm(raw_key)
    if not key:
        return None
    if key in _CANONICAL_LOAD_SET:
        return key

    stripped = key
    for prefix in ("load_", "consumption_", "demand_"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
            break

    if stripped in _CANONICAL_LOAD_SET:
        return stripped
    if stripped in {"house"}:
        return "house_a"
    if stripped in {"class_3"}:
        return "class3"
    if key in {"consumption", "demand"}:
        return "load"
    return None


def _load_aliases(canonical_key: str) -> List[str]:
    key = _norm(canonical_key)
    if key not in _CANONICAL_LOAD_SET:
        return []
    aliases = [key, f"load_{key}", f"consumption_{key}", f"demand_{key}"]
    if key == "house_a":
        aliases.append("house")
    if key == "class3":
        aliases.append("class_3")
    if key == "load":
        aliases.extend(["consumption", "demand"])

    out: List[str] = []
    seen = set()
    for alias in aliases:
        if alias in seen:
            continue
        seen.add(alias)
        out.append(alias)
    return out


def _normalize_load_series(
    load_map: Dict[str, Dict[int, float]],
) -> Dict[str, Dict[int, float]]:
    merged: Dict[str, Dict[int, Tuple[int, float]]] = {}
    for raw_key, series in (load_map or {}).items():
        canonical = _canonical_load_key(raw_key)
        if canonical is None or not isinstance(series, dict):
            continue
        priority = 0 if _norm(raw_key) == canonical else 1
        target = merged.setdefault(canonical, {})
        for raw_tick, raw_value in series.items():
            value = _parse_float(raw_value)
            if value is None:
                continue
            tick = int(raw_tick)
            current = target.get(tick)
            if current is None or priority < current[0]:
                target[tick] = (priority, float(value))

    out: Dict[str, Dict[int, float]] = {}
    for key in CANONICAL_LOAD_KEYS:
        raw_series = merged.get(key) or {}
        if not raw_series:
            continue
        out[key] = {
            int(tick): float(value)
            for tick, (_, value) in sorted(raw_series.items(), key=lambda item: item[0])
        }
    return out


def _build_class3_series(load_rows: Dict[str, Dict[int, float]]) -> Dict[int, float]:
    class3: Dict[int, float] = {}
    ticks = sorted(set().union(*[set(values.keys()) for values in load_rows.values()] or [set()]))
    for tick in ticks:
        total = 0.0
        for key in ("house_a", "house_b", "office", "hospital", "consumer", "load"):
            total += float((load_rows.get(key) or {}).get(tick, 0.0) or 0.0)
        if total > 0:
            class3[int(tick)] = float(total)
    return class3


def _with_load_aliases(load_rows: Dict[str, Dict[int, float]]) -> Dict[str, Dict[int, float]]:
    out: Dict[str, Dict[int, float]] = {
        key: dict(values) for key, values in (load_rows or {}).items()
    }
    for canonical, series in (load_rows or {}).items():
        for alias in _load_aliases(canonical):
            if alias in out:
                continue
            out[alias] = dict(series)
    return out


def _empty_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    return {"wind": {}, "solar": {}, "load": {}, "market": {}}


def _find_header(headers: List[str], names: Iterable[str]) -> Optional[str]:
    normalized = {_norm(header): header for header in headers}
    for candidate in names:
        key = _norm(candidate)
        if key in normalized:
            return normalized[key]
    return None


def _canonical_profile_key(raw_key: str) -> Optional[str]:
    key = _norm(raw_key)
    if key in _PROFILE_SET:
        return key
    if key in _LEGACY_LOAD_TO_PROFILE:
        return _LEGACY_LOAD_TO_PROFILE[key]
    for canonical, aliases in _PROFILE_ALIASES.items():
        if key in {_norm(alias) for alias in aliases}:
            return canonical
    return None


def _guess_columns(headers: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"factors": {}, "profiles": {}}
    out["tick"] = _find_header(headers, ("tick", "t", "step", "time"))
    for key in CANONICAL_FACTOR_KEYS:
        picked = _find_header(headers, _FACTOR_ALIASES.get(key, (key,)))
        if picked:
            out["factors"][key] = picked

    for header in headers:
        canonical = _canonical_profile_key(header)
        if canonical is None:
            continue
        out["profiles"].setdefault(canonical, header)
    return out


def _resolved_column_map(
    guessed: Dict[str, Any], incoming_map: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    out = {
        "tick": guessed.get("tick"),
        "factors": dict(guessed.get("factors") or {}),
        "profiles": dict(guessed.get("profiles") or {}),
    }
    if not incoming_map:
        return out

    if incoming_map.get("tick"):
        out["tick"] = incoming_map.get("tick")

    incoming_factors = incoming_map.get("factors")
    if isinstance(incoming_factors, dict):
        for key, value in incoming_factors.items():
            if _norm(key) in _FACTOR_SET and value:
                out["factors"][_norm(key)] = str(value)

    incoming_profiles = incoming_map.get("profiles")
    if isinstance(incoming_profiles, dict):
        for key, value in incoming_profiles.items():
            canonical = _canonical_profile_key(str(key))
            if canonical and value:
                out["profiles"][canonical] = str(value)

    return out


def _legacy_load_from_profile_key(profile_key: str) -> Optional[str]:
    key = _norm(profile_key)
    if key in _LEGACY_PROFILE_TO_LOAD:
        return _LEGACY_PROFILE_TO_LOAD[key]
    return None


def _display_labels_from_column_map(resolved_map: Dict[str, Any] | None) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    column_map = dict(resolved_map or {})

    for factor_key, source_column in dict(column_map.get("factors") or {}).items():
        canonical = _norm(str(factor_key))
        raw_name = str(source_column or "").strip()
        if canonical in _FACTOR_SET and raw_name:
            labels[canonical] = raw_name

    for profile_key, source_column in dict(column_map.get("profiles") or {}).items():
        canonical_profile = _canonical_profile_key(str(profile_key))
        raw_name = str(source_column or "").strip()
        if canonical_profile is None or not raw_name:
            continue
        labels[canonical_profile] = raw_name
        legacy_load_key = _legacy_load_from_profile_key(canonical_profile)
        if legacy_load_key:
            labels[legacy_load_key] = raw_name

    return labels


def _canonical_forecast_rows(
    periods: Iterable[ForecastPeriod],
) -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    ticks: List[int] = []
    for period in periods:
        tick = int(period.tick)
        ticks.append(tick)
        factor_source = dict(period.factors_json or {})
        profile_source = dict(period.profiles_json or {})
        if not factor_source:
            if period.wind is not None:
                factor_source["wind_factor"] = float(period.wind)
            if period.illumination is not None:
                factor_source["solar_factor"] = float(period.illumination)
            if period.market_price is not None:
                factor_source["market_price_buy"] = float(period.market_price)
        if not profile_source:
            for raw_key, raw_value in (period.consumption_json or {}).items():
                canonical = _canonical_profile_key(str(raw_key))
                if canonical is None:
                    continue
                parsed = _parse_float(raw_value)
                if parsed is None:
                    continue
                profile_source[canonical] = float(parsed)

        for key, value in factor_source.items():
            canonical_key = _norm(str(key))
            if canonical_key not in _FACTOR_SET:
                continue
            parsed = _parse_float(value)
            if parsed is None:
                continue
            factors.setdefault(canonical_key, {})[tick] = float(parsed)
        for key, value in profile_source.items():
            canonical_key = _canonical_profile_key(str(key))
            if canonical_key is None:
                continue
            parsed = _parse_float(value)
            if parsed is None:
                continue
            profiles.setdefault(canonical_key, {})[tick] = float(parsed)
    return factors, profiles, sorted(set(ticks))


def _empty_weather_analysis(message: str = "Недостаточно данных для анализа прогноза.") -> Dict[str, Any]:
    charts = {
        "generation_vs_consumption": {
            "title": "Генерация и потребление",
            "available": False,
            "reason": message,
            "priority": "primary",
        },
        "balance_uncertainty": {
            "title": "Баланс с коридором неопределённости",
            "available": False,
            "reason": message,
            "priority": "primary",
        },
        "wind_forecast": {
            "title": "Прогноз ветра",
            "available": False,
            "reason": message,
            "priority": "primary",
        },
        "consumption_categories": {
            "title": "Потребление по категориям",
            "available": False,
            "reason": message,
            "priority": "primary",
        },
        "solar_models": {
            "title": "Солнечная генерация: simple vs improved",
            "available": False,
            "reason": message,
            "priority": "secondary",
        },
        "solar_activity": {
            "title": "Солнечная активность east/west",
            "available": False,
            "reason": message,
            "priority": "secondary",
        },
        "generation_types": {
            "title": "Сравнение источников генерации",
            "available": False,
            "reason": message,
            "priority": "secondary",
        },
        "source_mix": {
            "title": "Структура генерации и потребление",
            "available": False,
            "reason": message,
            "priority": "secondary",
        },
    }
    return {
        "mode": "partial",
        "availability": {
            "has_wind_range": False,
            "has_solar_east_west": False,
            "has_category_breakdown": False,
        },
        "kpis": {
            "avg_generation": None,
            "avg_consumption": None,
            "avg_balance": None,
            "deficit_count": 0,
            "surplus_count": 0,
            "max_deficit": {"value": None, "tick": None},
            "max_surplus": {"value": None, "tick": None},
            "wind_off_count": 0,
            "wind_full_power_count": 0,
            "solar_share_pct": None,
            "wind_share_pct": None,
        },
        "series": {
            "tick": [],
            "status": [],
            "total_consumption": [],
            "total_generation": [],
            "balance": [],
            "balance_min": [],
            "balance_max": [],
            "wind_avg": [],
            "wind_gen": [],
            "solar_improved": [],
            "solar_simple": [],
            "solar_east_gen": [],
            "solar_west_gen": [],
            "sun_east": [],
            "sun_west": [],
            "category_consumption": {key: [] for key in WEATHER_CATEGORY_SERIES_ORDER},
        },
        "charts": charts,
        "decision_support": {"headline": message, "cards": []},
        "tables": {"main_stats": [], "generator_stats": []},
        "insights": [message],
    }


def _weather_raw_series_from_periods(periods: Iterable[ForecastPeriod]) -> Dict[str, Dict[int, float]]:
    raw_series: Dict[str, Dict[int, float]] = {}
    for period in periods:
        tick = int(period.tick)
        extra = dict(period.extra_json or {})
        raw_columns = dict(extra.get("raw_columns") or {})
        combined: Dict[str, Any] = {}
        for key, value in extra.items():
            if key == "raw_columns":
                continue
            combined[str(key)] = value
        combined.update(raw_columns)
        for raw_key, raw_value in combined.items():
            key = _norm(str(raw_key))
            if not key:
                continue
            parsed = _parse_float(raw_value)
            if parsed is None:
                continue
            raw_series.setdefault(key, {})[tick] = float(parsed)
    return raw_series


def _pick_weather_raw_series(
    raw_series: Dict[str, Dict[int, float]], *aliases: str
) -> Dict[int, float]:
    for alias in aliases:
        key = _norm(alias)
        series = raw_series.get(key) or {}
        if series:
            return {int(tick): float(value) for tick, value in series.items()}
    return {}


def _pick_weather_alias_series(
    raw_series: Dict[str, Dict[int, float]], key: str
) -> Dict[int, float]:
    return _pick_weather_raw_series(raw_series, *(WEATHER_RAW_ALIASES.get(key) or (key,)))


def _compute_solar_from_east_west(
    ticks: List[int],
    sun_east: Dict[int, float],
    sun_west: Dict[int, float],
) -> Dict[str, Dict[int, float]]:
    solar_east_gen: Dict[int, float] = {}
    solar_west_gen: Dict[int, float] = {}
    solar_simple: Dict[int, float] = {}
    solar_improved: Dict[int, float] = {}
    solar_min: Dict[int, float] = {}
    solar_max: Dict[int, float] = {}

    for tick in ticks:
        east = _series_value(sun_east, tick)
        west = _series_value(sun_west, tick)
        east_gen = clamp(0.91 * east + 2.81, 0.0, 25.0) if east is not None else None
        west_gen = clamp(0.91 * west + 2.81, 0.0, 25.0) if west is not None else None
        if east_gen is not None:
            solar_east_gen[tick] = float(east_gen)
        if west_gen is not None:
            solar_west_gen[tick] = float(west_gen)
        if east_gen is None and west_gen is None:
            continue
        if east_gen is not None and west_gen is not None:
            simple = (east_gen + west_gen) / 2.0
            improved = simple
            if east == 0 and west == 0:
                improved = 0.0
            elif east == 0:
                improved = west_gen
            elif west == 0:
                improved = east_gen
        else:
            simple = east_gen if east_gen is not None else west_gen
            improved = simple
        if simple is None or improved is None:
            continue
        solar_simple[tick] = float(simple)
        solar_improved[tick] = float(improved)
        solar_min[tick] = float(max(0.0, improved - 0.455))
        solar_max[tick] = float(min(25.0, improved + 0.455))

    return {
        "solar_east_gen": solar_east_gen,
        "solar_west_gen": solar_west_gen,
        "solar_simple": solar_simple,
        "solar_improved": solar_improved,
        "solar_min": solar_min,
        "solar_max": solar_max,
    }


def _compute_solar_from_single_factor(ticks: List[int], solar_factor: Dict[int, float]) -> Dict[str, Dict[int, float]]:
    solar_improved: Dict[int, float] = {}
    solar_min: Dict[int, float] = {}
    solar_max: Dict[int, float] = {}
    for tick in ticks:
        solar_value = _series_value(solar_factor, tick)
        if solar_value is None:
            continue
        improved = clamp(0.91 * solar_value + 2.81, 0.0, 25.0)
        solar_improved[tick] = float(improved)
        solar_min[tick] = float(max(0.0, improved - 0.455))
        solar_max[tick] = float(min(25.0, improved + 0.455))
    return {
        "solar_east_gen": {},
        "solar_west_gen": {},
        "solar_simple": {},
        "solar_improved": solar_improved,
        "solar_min": solar_min,
        "solar_max": solar_max,
    }


def _compute_wind_from_average(ticks: List[int], wind_avg_source: Dict[int, float]) -> Dict[str, Dict[int, float]]:
    wind_avg: Dict[int, float] = {}
    wind_gen: Dict[int, float] = {}
    for tick in ticks:
        avg_value = _series_value(wind_avg_source, tick)
        if avg_value is None:
            continue
        wind_avg[tick] = float(avg_value)
        generated = min(avg_value * 8.0 / 6.0, 8.0)
        if avg_value > 7.0:
            generated = 0.0
        wind_gen[tick] = float(generated)
    return {"wind_avg": wind_avg, "wind_gen": wind_gen}


def _compute_wind_from_range(
    ticks: List[int],
    wind_from: Dict[int, float],
    wind_to: Dict[int, float],
) -> Dict[str, Dict[int, float]]:
    wind_avg: Dict[int, float] = {}
    for tick in ticks:
        low = _series_value(wind_from, tick)
        high = _series_value(wind_to, tick)
        if low is None and high is None:
            continue
        if low is None:
            avg_value = float(high)
        elif high is None:
            avg_value = float(low)
        else:
            avg_value = (low + high) / 2.0
        wind_avg[tick] = float(avg_value)
    computed = _compute_wind_from_average(ticks, wind_avg)
    computed["wind_from"] = {int(tick): float(value) for tick, value in wind_from.items()}
    computed["wind_to"] = {int(tick): float(value) for tick, value in wind_to.items()}
    return computed


def _compute_total_consumption(
    ticks: List[int],
    category_series: Dict[str, Dict[int, float]],
) -> Dict[str, Any]:
    total_consumption: Dict[int, float] = {}
    used_categories = {
        key: {int(tick): float(value) for tick, value in values.items()}
        for key, values in category_series.items()
        if values
    }
    for tick in ticks:
        values = [_series_value(series, tick) for series in used_categories.values()]
        total = _sum_available(*values)
        if total is None:
            continue
        total_consumption[tick] = float(total)
    return {
        "total_consumption": total_consumption,
        "used_categories": used_categories,
    }


def _compute_generation_balance_metrics(
    *,
    ticks: List[int],
    total_consumption: Dict[int, float],
    solar_improved: Dict[int, float],
    solar_min: Dict[int, float],
    solar_max: Dict[int, float],
    wind_gen: Dict[int, float],
    consumption_uncertainty: float,
) -> Dict[str, Dict[int, float] | Dict[int, str]]:
    total_generation: Dict[int, float] = {}
    balance: Dict[int, float] = {}
    balance_min: Dict[int, float] = {}
    balance_max: Dict[int, float] = {}
    consumption_min: Dict[int, float] = {}
    consumption_max: Dict[int, float] = {}
    status: Dict[int, str] = {}

    for tick in ticks:
        generation = _sum_available(_series_value(solar_improved, tick), _series_value(wind_gen, tick))
        consumption = _series_value(total_consumption, tick)
        if generation is not None:
            total_generation[tick] = float(generation)
        if consumption is not None:
            consumption_min[tick] = float(consumption - consumption_uncertainty)
            consumption_max[tick] = float(consumption + consumption_uncertainty)
        if generation is None or consumption is None:
            continue
        balance_value = generation - consumption
        balance[tick] = float(balance_value)
        if balance_value < 0:
            status[tick] = "дефицит"
        elif balance_value > 0:
            status[tick] = "профицит"
        else:
            status[tick] = "баланс"
        low_solar = _series_value(solar_min, tick)
        high_solar = _series_value(solar_max, tick)
        wind_value = _series_value(wind_gen, tick)
        low_consumption = _series_value(consumption_min, tick)
        high_consumption = _series_value(consumption_max, tick)
        if low_solar is not None and wind_value is not None and high_consumption is not None:
            balance_min[tick] = float((wind_value + low_solar) - high_consumption)
        if high_solar is not None and wind_value is not None and low_consumption is not None:
            balance_max[tick] = float((wind_value + high_solar) - low_consumption)

    return {
        "total_generation": total_generation,
        "balance": balance,
        "balance_min": balance_min,
        "balance_max": balance_max,
        "consumption_min": consumption_min,
        "consumption_max": consumption_max,
        "status": status,
    }


def _series_extreme_with_tick(
    ticks: List[int], series: List[Optional[float]], *, direction: str, predicate
) -> Dict[str, Optional[float] | Optional[int]]:
    pairs = [
        (int(tick), float(value))
        for tick, value in zip(ticks, series)
        if value is not None and predicate(float(value))
    ]
    if not pairs:
        return {"value": None, "tick": None}
    if direction == "min":
        tick, value = min(pairs, key=lambda item: item[1])
    else:
        tick, value = max(pairs, key=lambda item: item[1])
    return {"value": round(value, 4), "tick": int(tick)}


def _build_weather_table_row(
    key: str,
    label: str,
    series: List[Optional[float]],
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    if not series:
        return None
    values = _series_numbers(series)
    if not values:
        return None
    row = {
        "key": key,
        "label": label,
        "mean": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }
    row.update(extra)
    return row


def _build_weather_tables(series_payload: Dict[str, Any], kpis: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    main_stats: List[Dict[str, Any]] = []
    generator_stats: List[Dict[str, Any]] = []

    for key in ("total_consumption", "total_generation", "balance", "wind_avg", "solar_improved"):
        row = _build_weather_table_row(key, WEATHER_SERIES_LABELS[key], series_payload.get(key) or [])
        if row is not None:
            main_stats.append(row)

    solar_improved = list(series_payload.get("solar_improved") or [])
    wind_gen = list(series_payload.get("wind_gen") or [])
    solar_simple = list(series_payload.get("solar_simple") or [])
    solar_east_gen = list(series_payload.get("solar_east_gen") or [])
    solar_west_gen = list(series_payload.get("solar_west_gen") or [])

    solar_row = _build_weather_table_row(
        "solar_improved",
        WEATHER_SERIES_LABELS["solar_improved"],
        solar_improved,
        positive_count=sum(1 for value in solar_improved if value is not None and value > 0),
    )
    if solar_row is not None:
        generator_stats.append(solar_row)

    wind_row = _build_weather_table_row(
        "wind_gen",
        WEATHER_SERIES_LABELS["wind_gen"],
        wind_gen,
        wind_off_count=int(kpis.get("wind_off_count") or 0),
        full_power_count=int(kpis.get("wind_full_power_count") or 0),
    )
    if wind_row is not None:
        generator_stats.append(wind_row)

    for key, label, series in (
        ("solar_simple", WEATHER_SERIES_LABELS["solar_simple"], solar_simple),
        ("solar_east_gen", WEATHER_SERIES_LABELS["solar_east_gen"], solar_east_gen),
        ("solar_west_gen", WEATHER_SERIES_LABELS["solar_west_gen"], solar_west_gen),
    ):
        row = _build_weather_table_row(key, label, series)
        if row is not None:
            generator_stats.append(row)

    return {"main_stats": main_stats, "generator_stats": generator_stats}


def _weather_chart_available(series: List[Optional[float]] | List[int] | None) -> bool:
    if not isinstance(series, list) or not series:
        return False
    return any(value is not None for value in series)


def _build_weather_charts(
    *,
    availability: Dict[str, bool],
    series_payload: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    category_consumption = dict(series_payload.get("category_consumption") or {})
    category_has_values = any(_weather_chart_available(values) for values in category_consumption.values())
    has_generation = _weather_chart_available(series_payload.get("total_generation"))
    has_consumption = _weather_chart_available(series_payload.get("total_consumption"))
    has_balance = _weather_chart_available(series_payload.get("balance"))
    has_balance_min = _weather_chart_available(series_payload.get("balance_min"))
    has_balance_max = _weather_chart_available(series_payload.get("balance_max"))
    has_wind = _weather_chart_available(series_payload.get("wind_avg"))
    has_solar_mix = _weather_chart_available(series_payload.get("solar_improved")) or _weather_chart_available(
        series_payload.get("wind_gen")
    )

    return {
        "generation_vs_consumption": {
            "title": "Генерация и потребление",
            "available": has_generation and has_consumption,
            "reason": None
            if has_generation and has_consumption
            else "Недостаточно рядов для одновременного сравнения генерации и потребления.",
            "priority": "primary",
        },
        "balance_uncertainty": {
            "title": "Баланс с коридором неопределённости",
            "available": has_balance and has_balance_min and has_balance_max,
            "reason": None
            if has_balance and has_balance_min and has_balance_max
            else "Не удалось рассчитать полный коридор неопределённости.",
            "priority": "primary",
        },
        "wind_forecast": {
            "title": "Прогноз ветра и порог отключения",
            "available": has_wind,
            "reason": None if has_wind else "В прогнозе нет пригодного ряда ветра.",
            "priority": "primary",
        },
        "consumption_categories": {
            "title": "Потребление по категориям",
            "available": category_has_values,
            "reason": None
            if category_has_values
            else "В прогнозе нет разбивки потребления по категориям.",
            "priority": "primary",
        },
        "solar_models": {
            "title": "Солнечная генерация: simple vs improved",
            "available": availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("solar_simple"))
            and _weather_chart_available(series_payload.get("solar_improved")),
            "reason": None
            if availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("solar_simple"))
            and _weather_chart_available(series_payload.get("solar_improved"))
            else "Нужны отдельные ряды sun_east и sun_west.",
            "priority": "secondary",
        },
        "solar_activity": {
            "title": "Солнечная активность east/west",
            "available": availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("sun_east"))
            and _weather_chart_available(series_payload.get("sun_west")),
            "reason": None
            if availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("sun_east"))
            and _weather_chart_available(series_payload.get("sun_west"))
            else "Для этого графика нужны отдельные ряды east/west.",
            "priority": "secondary",
        },
        "generation_types": {
            "title": "Сравнение источников генерации",
            "available": has_solar_mix,
            "reason": None
            if has_solar_mix
            else "Недостаточно данных по солнечной или ветровой генерации.",
            "priority": "secondary",
        },
        "source_mix": {
            "title": "Структура генерации и потребление",
            "available": has_consumption and has_solar_mix,
            "reason": None
            if has_consumption and has_solar_mix
            else "Нужны и потребление, и хотя бы один источник генерации.",
            "priority": "secondary",
        },
    }


def _top_balance_ticks(
    ticks: List[int],
    balance_series: List[Optional[float]],
    *,
    sign: str,
    limit: int = 3,
) -> List[int]:
    pairs = [
        (int(tick), float(value))
        for tick, value in zip(ticks, balance_series)
        if value is not None and ((sign == "deficit" and value < 0) or (sign == "surplus" and value > 0))
    ]
    if sign == "deficit":
        ordered = sorted(pairs, key=lambda item: item[1])
    else:
        ordered = sorted(pairs, key=lambda item: item[1], reverse=True)
    return [tick for tick, _ in ordered[:limit]]


def _build_weather_decision_support(
    *,
    mode: str,
    kpis: Dict[str, Any],
    series_payload: Dict[str, Any],
) -> Dict[str, Any]:
    balance_series = list(series_payload.get("balance") or [])
    ticks = [int(tick) for tick in list(series_payload.get("tick") or [])]
    deficit_ticks = _top_balance_ticks(ticks, balance_series, sign="deficit", limit=3)
    surplus_ticks = _top_balance_ticks(ticks, balance_series, sign="surplus", limit=3)
    deficit_count = int(kpis.get("deficit_count") or 0)
    surplus_count = int(kpis.get("surplus_count") or 0)
    avg_balance = kpis.get("avg_balance")
    solar_share = kpis.get("solar_share_pct")
    wind_share = kpis.get("wind_share_pct")
    wind_off_count = int(kpis.get("wind_off_count") or 0)

    if avg_balance is not None and float(avg_balance) < -0.1:
        headline = "Покупать стоит генерацию и резерв: система чаще уходит в дефицит."
    elif avg_balance is not None and float(avg_balance) > 0.1:
        headline = "С генерацией запас есть: покупать её можно точечно, без перегруза портфеля."
    else:
        headline = "Баланс близок к нейтральному: лучше смотреть на устойчивость и качество профиля."

    strengthen_parts: List[str] = []
    if solar_share is not None and wind_share is not None:
        if float(solar_share) >= float(wind_share) + 10:
            strengthen_parts.append("солнечные лоты выглядят сильнее ветровых")
        elif float(wind_share) >= float(solar_share) + 10 and wind_off_count == 0:
            strengthen_parts.append("ветровые лоты выглядят сильнее солнечных")
    if deficit_count > surplus_count:
        strengthen_parts.append("полезны storage и смешанные пакеты генерации")
    elif surplus_count > deficit_count and surplus_count > 0:
        strengthen_parts.append("покупки генерации можно делать точечно, без агрессивного добора")
    if not strengthen_parts:
        strengthen_parts.append("лучше брать диверсифицированные лоты без перекоса в один источник")

    caution_parts: List[str] = []
    if wind_off_count > 0:
        caution_parts.append("ветровые лоты рискованнее из-за отключения турбины при сильном ветре")
    if mode == "partial":
        caution_parts.append("прогноз неполный, поэтому покупать узкоспециализированные лоты стоит осторожнее")
    if not caution_parts:
        caution_parts.append("не видно явного провала по источникам, но перегружать портфель одним типом генерации не стоит")

    timing_parts: List[str] = []
    if deficit_ticks:
        timing_parts.append("критичные такты: " + ", ".join(str(tick) for tick in deficit_ticks))
    if surplus_ticks:
        timing_parts.append("окна профицита: " + ", ".join(str(tick) for tick in surplus_ticks))
    if not timing_parts:
        timing_parts.append("резких провалов по тактам не видно")

    return {
        "headline": headline,
        "cards": [
            {"tone": "buy", "title": "Что усиливать", "text": "; ".join(strengthen_parts) + "."},
            {"tone": "watch", "title": "С чем осторожно", "text": "; ".join(caution_parts) + "."},
            {"tone": "timing", "title": "По каким тактам смотреть", "text": "; ".join(timing_parts) + "."},
        ],
    }


def _build_weather_insights(
    *,
    mode: str,
    availability: Dict[str, bool],
    kpis: Dict[str, Any],
    series_payload: Dict[str, Any],
) -> List[str]:
    insights: List[str] = []
    avg_balance = kpis.get("avg_balance")
    max_deficit = dict(kpis.get("max_deficit") or {})
    max_surplus = dict(kpis.get("max_surplus") or {})
    wind_off_count = int(kpis.get("wind_off_count") or 0)
    solar_share = kpis.get("solar_share_pct")
    wind_share = kpis.get("wind_share_pct")

    if mode == "full_nto_2024":
        insights.append(
            "Используется полный NTO-style анализ: учтены sun_east/sun_west, wind_from/wind_to и разбиение нагрузки по категориям."
        )
    elif mode == "canonical_fallback":
        insights.append(
            "Используется canonical fallback: расчёт опирается на wind_factor, solar_factor и доступные canonical load series."
        )
    else:
        insights.append(
            "Анализ частичный: часть исходных рядов отсутствует, поэтому выводы построены только по доступным данным."
        )

    if not availability.get("has_solar_east_west"):
        insights.append("Графики east/west отключены, потому что прогноз не содержит отдельных рядов sun_east и sun_west.")

    if avg_balance is not None:
        if float(avg_balance) < -0.1:
            insights.append("В среднем наблюдается дефицит энергии.")
        elif float(avg_balance) > 0.1:
            insights.append("В среднем наблюдается профицит энергии.")
        else:
            insights.append("Средний баланс близок к нулю.")

    if max_deficit.get("tick") is not None and max_deficit.get("value") is not None:
        insights.append(
            f"Максимальный дефицит приходится на такт {int(max_deficit['tick'])}: {abs(float(max_deficit['value'])):.2f}."
        )
    elif max_surplus.get("tick") is not None and max_surplus.get("value") is not None:
        insights.append(
            f"Максимальный профицит приходится на такт {int(max_surplus['tick'])}: {float(max_surplus['value']):.2f}."
        )

    if wind_off_count > 0:
        insights.append(f"Ветряк отключается из-за сильного ветра в {wind_off_count} тактах.")

    if solar_share is not None and wind_share is not None:
        if float(solar_share) >= float(wind_share) + 10:
            insights.append("Солнечная генерация доминирует над ветровой.")
        elif float(wind_share) >= float(solar_share) + 10:
            insights.append("Ветровая генерация доминирует над солнечной.")

    generation_max = _series_max(series_payload.get("total_generation") or [])
    consumption_max = _series_max(series_payload.get("total_consumption") or [])
    if generation_max is not None and consumption_max is not None and consumption_max > generation_max:
        insights.append("Пик потребления заметно выше пиковой генерации.")

    if not availability.get("has_category_breakdown"):
        insights.append("Детальная разбивка потребления по категориям недоступна для этого формата прогноза.")

    unique_insights: List[str] = []
    for item in insights:
        text = str(item).strip()
        if not text or text in unique_insights:
            continue
        unique_insights.append(text)
    return unique_insights[:8]


def build_weather_analysis_from_periods(
    *,
    periods: Iterable[ForecastPeriod],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    ticks: List[int],
) -> Dict[str, Any]:
    ordered_ticks = [int(tick) for tick in sorted(set(ticks))]
    if not ordered_ticks:
        return _empty_weather_analysis()

    raw_series = _weather_raw_series_from_periods(periods)
    wind_from = _pick_weather_alias_series(raw_series, "wind_from")
    wind_to = _pick_weather_alias_series(raw_series, "wind_to")
    sun_east = _pick_weather_alias_series(raw_series, "sun_east")
    sun_west = _pick_weather_alias_series(raw_series, "sun_west")

    raw_category_series = {
        "hospital": _pick_weather_alias_series(raw_series, "hospital"),
        "factory": _pick_weather_alias_series(raw_series, "factory"),
        "house_a": _pick_weather_alias_series(raw_series, "house_a"),
        "house_b": _pick_weather_alias_series(raw_series, "house_b"),
        "office": _pick_weather_alias_series(raw_series, "office"),
    }
    canonical_category_series = {
        "factory": {int(tick): float(value) for tick, value in (profiles.get("factory_load") or {}).items()},
        "office": {int(tick): float(value) for tick, value in (profiles.get("office_load") or {}).items()},
        "house_load": {int(tick): float(value) for tick, value in (profiles.get("house_load") or {}).items()},
    }

    has_wind_range = _series_has_values(wind_from) and _series_has_values(wind_to)
    has_solar_east_west = _series_has_values(sun_east) and _series_has_values(sun_west)
    has_full_categories = all(
        _series_has_values(raw_category_series.get(key))
        for key in ("hospital", "factory", "house_a", "house_b")
    )
    has_canonical_generation = bool((factors.get("wind_factor") or {}) and (factors.get("solar_factor") or {}))
    has_canonical_loads = any(values for values in canonical_category_series.values())

    if has_wind_range and has_solar_east_west and has_full_categories:
        mode = "full_nto_2024"
    elif has_canonical_generation and has_canonical_loads:
        mode = "canonical_fallback"
    else:
        mode = "partial"

    if mode == "full_nto_2024":
        selected_categories = {
            key: dict(raw_category_series[key])
            for key in ("hospital", "factory", "house_a", "house_b")
        }
        solar_computed = _compute_solar_from_east_west(ordered_ticks, sun_east, sun_west)
        wind_computed = _compute_wind_from_range(ordered_ticks, wind_from, wind_to)
        consumption_uncertainty = 2.0
    elif mode == "canonical_fallback":
        selected_categories = {
            key: dict(values) for key, values in canonical_category_series.items() if values
        }
        solar_computed = _compute_solar_from_single_factor(
            ordered_ticks, dict(factors.get("solar_factor") or {})
        )
        wind_computed = _compute_wind_from_average(
            ordered_ticks, dict(factors.get("wind_factor") or {})
        )
        consumption_uncertainty = 0.5 * len(selected_categories)
    else:
        selected_categories = {
            key: dict(values) for key, values in raw_category_series.items() if values
        }
        if not selected_categories:
            selected_categories = {
                key: dict(values) for key, values in canonical_category_series.items() if values
            }
        if _series_has_values(sun_east) or _series_has_values(sun_west):
            solar_computed = _compute_solar_from_east_west(ordered_ticks, sun_east, sun_west)
        else:
            solar_computed = _compute_solar_from_single_factor(
                ordered_ticks, dict(factors.get("solar_factor") or {})
            )
        if _series_has_values(wind_from) or _series_has_values(wind_to):
            wind_computed = _compute_wind_from_range(ordered_ticks, wind_from, wind_to)
        else:
            wind_computed = _compute_wind_from_average(
                ordered_ticks, dict(factors.get("wind_factor") or {})
            )
        consumption_uncertainty = 0.5 * len(selected_categories)

    consumption_computed = _compute_total_consumption(ordered_ticks, selected_categories)
    balance_metrics = _compute_generation_balance_metrics(
        ticks=ordered_ticks,
        total_consumption=dict(consumption_computed["total_consumption"]),
        solar_improved=dict(solar_computed["solar_improved"]),
        solar_min=dict(solar_computed["solar_min"]),
        solar_max=dict(solar_computed["solar_max"]),
        wind_gen=dict(wind_computed["wind_gen"]),
        consumption_uncertainty=consumption_uncertainty,
    )

    category_payload = {
        "hospital": _series_payload(ordered_ticks, selected_categories.get("hospital")),
        "factory": _series_payload(ordered_ticks, selected_categories.get("factory")),
        "house_a": _series_payload(ordered_ticks, selected_categories.get("house_a")),
        "house_b": _series_payload(ordered_ticks, selected_categories.get("house_b")),
        "office": _series_payload(ordered_ticks, selected_categories.get("office")),
        "house_load": _series_payload(ordered_ticks, selected_categories.get("house_load")),
    }

    series_payload = {
        "tick": ordered_ticks,
        "status": [
            str((balance_metrics.get("status") or {}).get(int(tick)) or "")
            for tick in ordered_ticks
        ],
        "total_consumption": _series_payload(
            ordered_ticks, dict(consumption_computed["total_consumption"])
        ),
        "total_generation": _series_payload(ordered_ticks, balance_metrics.get("total_generation")),
        "balance": _series_payload(ordered_ticks, balance_metrics.get("balance")),
        "balance_min": _series_payload(ordered_ticks, balance_metrics.get("balance_min")),
        "balance_max": _series_payload(ordered_ticks, balance_metrics.get("balance_max")),
        "wind_avg": _series_payload(ordered_ticks, wind_computed.get("wind_avg")),
        "wind_gen": _series_payload(ordered_ticks, wind_computed.get("wind_gen")),
        "solar_improved": _series_payload(ordered_ticks, solar_computed.get("solar_improved")),
        "solar_simple": _series_payload(ordered_ticks, solar_computed.get("solar_simple")),
        "solar_east_gen": _series_payload(ordered_ticks, solar_computed.get("solar_east_gen")),
        "solar_west_gen": _series_payload(ordered_ticks, solar_computed.get("solar_west_gen")),
        "sun_east": _series_payload(ordered_ticks, sun_east),
        "sun_west": _series_payload(ordered_ticks, sun_west),
        "category_consumption": category_payload,
    }

    total_generation_series = list(series_payload["total_generation"])
    total_consumption_series = list(series_payload["total_consumption"])
    balance_series = list(series_payload["balance"])
    wind_avg_series = list(series_payload["wind_avg"])
    wind_gen_series = list(series_payload["wind_gen"])
    solar_improved_series = list(series_payload["solar_improved"])

    generation_sum = sum(_series_numbers(total_generation_series))
    solar_generation_sum = sum(_series_numbers(solar_improved_series))
    wind_generation_sum = sum(_series_numbers(wind_gen_series))

    kpis = {
        "avg_generation": _series_mean(total_generation_series),
        "avg_consumption": _series_mean(total_consumption_series),
        "avg_balance": _series_mean(balance_series),
        "deficit_count": sum(1 for value in balance_series if value is not None and value < 0),
        "surplus_count": sum(1 for value in balance_series if value is not None and value > 0),
        "max_deficit": _series_extreme_with_tick(
            ordered_ticks, balance_series, direction="min", predicate=lambda value: value < 0
        ),
        "max_surplus": _series_extreme_with_tick(
            ordered_ticks, balance_series, direction="max", predicate=lambda value: value > 0
        ),
        "wind_off_count": sum(1 for value in wind_avg_series if value is not None and value > 7),
        "wind_full_power_count": sum(
            1 for value in wind_gen_series if value is not None and value >= 8.0
        ),
        "solar_share_pct": round((solar_generation_sum / generation_sum) * 100.0, 2)
        if generation_sum > 0
        else None,
        "wind_share_pct": round((wind_generation_sum / generation_sum) * 100.0, 2)
        if generation_sum > 0
        else None,
    }

    availability = {
        "has_wind_range": has_wind_range,
        "has_solar_east_west": has_solar_east_west,
        "has_category_breakdown": any(values for values in category_payload.values()),
    }

    charts = _build_weather_charts(availability=availability, series_payload=series_payload)
    decision_support = _build_weather_decision_support(
        mode=mode,
        kpis=kpis,
        series_payload=series_payload,
    )
    tables = _build_weather_tables(series_payload, kpis)
    insights = _build_weather_insights(
        mode=mode,
        availability=availability,
        kpis=kpis,
        series_payload=series_payload,
    )

    return {
        "mode": mode,
        "availability": availability,
        "kpis": kpis,
        "series": series_payload,
        "charts": charts,
        "decision_support": decision_support,
        "tables": tables,
        "insights": insights,
    }


def _required_forecast_links(object_type: ObjectType) -> Dict[str, List[str]]:
    required_profiles: List[str] = []
    required_factors: List[str] = []

    explicit_profile = _norm(getattr(object_type, "forecast_profile_key", "") or "")
    if explicit_profile:
        canonical = _canonical_profile_key(explicit_profile)
        if canonical:
            required_profiles.append(canonical)

    explicit_deps = list(getattr(object_type, "resource_dependencies_json", []) or [])
    for item in explicit_deps:
        key = _norm(str(item))
        if key in _FACTOR_SET and key not in required_factors:
            required_factors.append(key)

    defaults = dict(object_type.default_parameters_json or {})
    fallback_profile = _canonical_profile_key(str(defaults.get("profile", "") or ""))
    if fallback_profile and fallback_profile not in required_profiles:
        required_profiles.append(fallback_profile)

    if bool(defaults.get("depends_on_sun")) and "solar_factor" not in required_factors:
        required_factors.append("solar_factor")
    if bool(defaults.get("depends_on_wind")) and "wind_factor" not in required_factors:
        required_factors.append("wind_factor")
    optional_profiles: List[str] = []
    optional_factors: List[str] = []
    model_type = _norm(getattr(object_type, "forecast_model_type", "") or "")
    economic_role = _norm(getattr(object_type, "economic_role", "") or "")
    category = _norm(getattr(object_type, "category", "") or "")
    code = _norm(getattr(object_type, "code", "") or "")

    resolved_role = (
        economic_role
        if economic_role in {"consumer", "generator", "storage", "infrastructure", "mixed"}
        else category
    )

    if resolved_role == "consumer":
        if not required_profiles:
            if code == "factory":
                required_profiles.append("factory_load")
            elif code == "hospital":
                required_profiles.append("hospital_load")
            elif code == "office":
                required_profiles.append("office_load")
            elif code in {"house_b", "houseb"}:
                required_profiles.append("house_b_load")
            else:
                required_profiles.append("house_a_load")
    elif resolved_role == "generator":
        if code in {"wind", "tps"} and "wind_factor" not in required_factors:
            required_factors.append("wind_factor")
        if code in {"solar", "cyber_solar", "solarrobot"} and "solar_factor" not in required_factors:
            required_factors.append("solar_factor")
        if model_type in {"wind_factor_curve", "solar_factor_output", "wind_output_2026", "solar_output_2026"}:
            optional_profiles.extend(required_profiles)
            required_profiles = []
    elif resolved_role == "storage":
        optional_profiles.extend(required_profiles)
        required_profiles = []
    elif resolved_role == "infrastructure":
        optional_profiles.extend(required_profiles)
        required_profiles = []

    if model_type in {"dispatchable_thermal", "infrastructure_constraint", "storage_dispatch"}:
        optional_profiles.extend(required_profiles)
        required_profiles = []

    return {
        "required_profiles": sorted(set(required_profiles)),
        "required_factors": sorted(set(required_factors)),
        "optional_profiles": sorted(set(optional_profiles)),
        "optional_factors": sorted(set(optional_factors)),
    }


def build_forecast_compatibility_report(
    *,
    session: GameSession,
    ticks: List[int],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    unused_columns: List[str],
    normalization_map: Dict[str, Any],
) -> Dict[str, Any]:
    horizon = int(
        ((session.ruleset.config_json or {}).get("time", {}) or {}).get("horizon_ticks", 48) or 48
    )
    blocking_reasons: List[str] = []
    if len(ticks) != horizon:
        blocking_reasons.append(
            f"Горизонт прогноза {len(ticks)} тактов не совпадает с ruleset ({horizon})."
        )

    used_types: Dict[int, ObjectType] = {}
    for obj in session.objects:
        if obj.object_type is not None and obj.is_active:
            used_types[int(obj.object_type.id)] = obj.object_type
    for lot in session.lots:
        for item in lot.items:
            if item.object_type is not None:
                used_types[int(item.object_type.id)] = item.object_type

    rows: List[Dict[str, Any]] = []
    covered_types: List[str] = []
    partial_types: List[str] = []
    missing_types: List[str] = []

    for object_type in sorted(used_types.values(), key=lambda row: row.code):
        links = _required_forecast_links(object_type)
        required_profiles = list(links["required_profiles"])
        required_factors = list(links["required_factors"])
        optional_profiles = list(links["optional_profiles"])
        optional_factors = list(links["optional_factors"])
        missing_profiles: List[str] = []
        partial_profiles: List[str] = []
        for key in required_profiles:
            series = profiles.get(key) or {}
            if not series:
                missing_profiles.append(key)
                continue
            if ticks and len(series) < len(ticks):
                partial_profiles.append(key)
        missing_factors: List[str] = []
        partial_factors: List[str] = []
        for key in required_factors:
            series = factors.get(key) or {}
            if not series:
                missing_factors.append(key)
                continue
            if ticks and len(series) < len(ticks):
                partial_factors.append(key)
        optional_profiles_present: List[str] = []
        for key in optional_profiles:
            series = profiles.get(key) or {}
            if series:
                optional_profiles_present.append(key)
        optional_factors_present: List[str] = []
        for key in optional_factors:
            series = factors.get(key) or {}
            if series:
                optional_factors_present.append(key)

        if missing_profiles or missing_factors:
            status = "missing"
            missing_types.append(object_type.code)
        elif partial_profiles or partial_factors:
            status = "partial"
            partial_types.append(object_type.code)
        else:
            status = "covered"
            covered_types.append(object_type.code)

        rows.append(
            {
                "object_type_code": object_type.code,
                "object_type_name": object_type.name,
                "status": status,
                "required_profiles": required_profiles,
                "required_factors": required_factors,
                "optional_profiles": optional_profiles,
                "optional_factors": optional_factors,
                "missing_profiles": missing_profiles,
                "missing_factors": missing_factors,
                "partial_profiles": partial_profiles,
                "partial_factors": partial_factors,
                "optional_profiles_present": optional_profiles_present,
                "optional_factors_present": optional_factors_present,
            }
        )

    if missing_types:
        blocking_reasons.append(
            "Нет полного покрытия прогноза для типов объектов: "
            + ", ".join(sorted(set(missing_types)))
        )
    if partial_types:
        blocking_reasons.append(
            "Частичное покрытие прогноза для типов объектов: "
            + ", ".join(sorted(set(partial_types)))
        )

    return {
        "is_compatible": len(blocking_reasons) == 0,
        "blocking_reasons": blocking_reasons,
        "horizon_expected": horizon,
        "horizon_actual": len(ticks),
        "ticks": ticks,
        "covered_types": sorted(set(covered_types)),
        "partial_types": sorted(set(partial_types)),
        "missing_types": sorted(set(missing_types)),
        "rows": rows,
        "unused_columns": sorted(set(unused_columns)),
        "normalization_map": dict(normalization_map),
    }


def _quality_summary(
    *,
    headers: Iterable[str],
    warnings: List[str],
    series_stats: Dict[str, Dict[str, float] | None],
) -> Dict[str, Any]:
    required = {"wind_factor", "solar_factor", "market_price_buy"}
    problem_columns: List[str] = []
    empty_columns: List[str] = []
    for key, stats in series_stats.items():
        if stats is None:
            empty_columns.append(key)
            if key in required:
                problem_columns.append(key)
            continue
        if abs(float(stats["max"]) - float(stats["min"])) < 1e-9:
            problem_columns.append(key)
    unique_headers = sorted({_norm(header) for header in headers if header})
    for key in required:
        if key not in unique_headers and key not in problem_columns:
            problem_columns.append(key)
    if problem_columns:
        text = "Есть проблемные ряды: " + ", ".join(problem_columns)
    elif warnings:
        text = "Прогноз пригоден для оценки, но содержит предупреждения по данным."
    else:
        text = "Прогноз пригоден для оценки: ключевые ряды заполнены."
    return {
        "warnings": list(warnings),
        "problem_columns": sorted(set(problem_columns)),
        "empty_columns": sorted(set(empty_columns)),
        "text": text,
    }


@lru_cache(maxsize=1)
def _cached_bundled_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    if not DEFAULT_FORECASTS_DIR.exists():
        return _empty_forecast_pack()
    return load_forecasts(str(DEFAULT_FORECASTS_DIR))


def load_bundled_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    return copy.deepcopy(_cached_bundled_forecast_pack())


def is_forecast_pack_empty(pack: Dict[str, Dict[str, Dict[int, float]]] | None) -> bool:
    if not isinstance(pack, dict):
        return True
    for bucket in pack.values():
        if isinstance(bucket, dict) and bucket:
            return False
    return True


def _bundled_canonical_dataset() -> (
    Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]
):
    pack = load_bundled_forecast_pack()
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    wind_bucket = dict(pack.get("wind", {}) or {})
    wind_rows = (wind_bucket.get("wind", {}) or {})
    if not wind_rows and wind_bucket:
        ticks = sorted({int(tick) for series in wind_bucket.values() for tick in series.keys()})
        wind_rows = {
            int(tick): _mean([float((series or {}).get(int(tick), 0.0) or 0.0) for series in wind_bucket.values()])
            for tick in ticks
        }
    solar_rows = (pack.get("solar", {}) or {}).get("solar", {}) or {}
    market_rows = (pack.get("market", {}) or {}).get("price", {}) or {}
    if wind_rows:
        factors["wind_factor"] = {int(k): float(v) for k, v in wind_rows.items()}
    if solar_rows:
        factors["solar_factor"] = {int(k): float(v) for k, v in solar_rows.items()}
    if market_rows:
        factors["market_price_buy"] = {int(k): float(v) for k, v in market_rows.items()}
    load_rows = pack.get("load", {}) or {}
    for raw_key, series in load_rows.items():
        canonical = _canonical_profile_key(raw_key)
        if canonical is None:
            continue
        target = profiles.setdefault(canonical, {})
        for tick, value in (series or {}).items():
            parsed = _parse_float(value)
            if parsed is None:
                continue
            target[int(tick)] = float(parsed)
    ticks = sorted(
        {
            *{int(tick) for tick in factors.get("wind_factor", {}).keys()},
            *{int(tick) for tick in factors.get("solar_factor", {}).keys()},
            *{int(tick) for tick in factors.get("market_price_buy", {}).keys()},
            *{int(tick) for values in profiles.values() for tick in values.keys()},
        }
    )
    return factors, profiles, ticks


def bundled_forecast_summary() -> Dict[str, Any]:
    factors, profiles, ticks = _bundled_canonical_dataset()

    def avg(values: Iterable[float]) -> Optional[float]:
        rows = [float(value) for value in values]
        if not rows:
            return None
        return round(sum(rows) / len(rows), 4)

    series_stats: Dict[str, Dict[str, float] | None] = {}
    for key in CANONICAL_FACTOR_KEYS:
        series_stats[key] = _series_stats((factors.get(key) or {}).values())
    for key in CANONICAL_PROFILE_KEYS:
        series_stats[key] = _series_stats((profiles.get(key) or {}).values())

    load_rows: Dict[str, Dict[int, float]] = {}
    for profile_key, legacy_key in _LEGACY_PROFILE_TO_LOAD.items():
        if profile_key in profiles:
            load_rows[legacy_key] = dict(profiles[profile_key])
    if "class3" not in load_rows:
        class3 = _build_class3_series(load_rows)
        if class3:
            load_rows["class3"] = class3
    load_series_display = _build_load_series_display(load_rows)
    series_stats_display = _build_series_stats_display(series_stats, load_rows)

    summary = {
        "mode": "builtin",
        "source_kind": "bundled_forecast",
        "forecast_id": None,
        "name": TEST_GAME_BUNDLED_FORECAST_NAME,
        "source_file": str(DEFAULT_FORECASTS_DIR),
        "count": len(ticks),
        "tick_from": ticks[0] if ticks else None,
        "tick_to": ticks[-1] if ticks else None,
        "avg_wind": avg((factors.get("wind_factor") or {}).values()),
        "avg_illumination": avg((factors.get("solar_factor") or {}).values()),
        "avg_market_price": avg((factors.get("market_price_buy") or {}).values()),
        "factors_keys": sorted(factors.keys()),
        "profiles_keys": sorted(profiles.keys()),
        "load_series": sorted(load_rows.keys()),
        "load_series_display": load_series_display,
        "load_series_raw": sorted(load_rows.keys()),
        "raw_csv_columns": [],
        "used_raw_columns": [],
        "unsupported_raw_columns": [],
        "column_mapping_rows": [],
        "mapped_raw_columns": [],
        "mapped_raw_stats_display": [],
        "mapped_tick_range_label": format_tick_range(
            ticks[0] if ticks else None,
            ticks[-1] if ticks else None,
            periods=len(ticks),
        ),
        "consumer_averages": {k: avg(v.values()) for k, v in load_rows.items()},
        "series_stats": series_stats,
        "series_stats_display": series_stats_display,
        "internal_canonical_load_series": sorted(load_rows.keys()),
        "internal_canonical_series_stats": series_stats,
        "column_display_labels": {},
        "compatibility_report": {
            "is_compatible": True,
            "blocking_reasons": [],
            "covered_types": [],
            "partial_types": [],
            "missing_types": [],
            "rows": [],
            "unused_columns": [],
            "normalization_map": {},
        },
        "object_coverage_rows": [],
        "quality": _quality_summary(
            headers=[*CANONICAL_FACTOR_KEYS, *CANONICAL_PROFILE_KEYS],
            warnings=[],
            series_stats=series_stats,
        ),
        "warnings": [],
        "text": "Используется встроенный прогноз тестовой игры.",
    }
    summary["weather_analysis"] = build_weather_analysis_from_periods(
        periods=[],
        factors=factors,
        profiles=profiles,
        ticks=ticks,
    )
    return summary


def summarize_forecast_for_session(*, session: GameSession, forecast: Forecast) -> Dict[str, Any]:
    summary = summarize_forecast(forecast)
    factors, profiles, ticks = _canonical_forecast_rows(list(forecast.periods))
    normalization_map = dict(forecast.normalization_map_json or {})
    resolved_map = dict(normalization_map.get("resolved") or forecast.column_map_json or {})
    unused_columns = list(normalization_map.get("unused_columns") or [])
    compatibility_report = build_forecast_compatibility_report(
        session=session,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        unused_columns=unused_columns,
        normalization_map=resolved_map,
    )
    summary["compatibility_report"] = compatibility_report
    summary["is_compatible"] = bool(compatibility_report.get("is_compatible"))
    summary["incompatibility_reason"] = "; ".join(
        compatibility_report.get("blocking_reasons") or []
    )
    if not summary["is_compatible"]:
        summary["text"] = (
            f"{summary['text']} Блокировка оценки: "
            f"{summary['incompatibility_reason'] or 'неполное покрытие прогноза'}."
        ).strip()
    return summary


def bundled_forecast_summary_for_session(session: GameSession) -> Dict[str, Any]:
    summary = bundled_forecast_summary()
    factors, profiles, ticks = _bundled_canonical_dataset()
    compatibility_report = build_forecast_compatibility_report(
        session=session,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        unused_columns=[],
        normalization_map={},
    )
    summary["compatibility_report"] = compatibility_report
    summary["is_compatible"] = bool(compatibility_report.get("is_compatible"))
    summary["incompatibility_reason"] = "; ".join(
        compatibility_report.get("blocking_reasons") or []
    )
    if not summary["is_compatible"]:
        summary["text"] = (
            f"{summary['text']} Блокировка оценки: "
            f"{summary['incompatibility_reason'] or 'неполное покрытие прогноза'}."
        ).strip()
    return summary


def session_forecast_compatibility(
    *,
    session: GameSession,
    forecast: Optional[Forecast],
) -> Dict[str, Any]:
    if forecast is None:
        summary = bundled_forecast_summary_for_session(session)
    else:
        summary = summarize_forecast_for_session(session=session, forecast=forecast)
    return {
        "session_id": int(session.id),
        "forecast_id": int(forecast.id) if forecast is not None else None,
        "forecast_name": summary.get("name") or TEST_GAME_BUNDLED_FORECAST_NAME,
        "is_compatible": bool(summary.get("is_compatible", True)),
        "incompatibility_reason": summary.get("incompatibility_reason") or "",
        "compatibility_report": dict(summary.get("compatibility_report") or {}),
        "profiles_keys": list(summary.get("profiles_keys") or []),
        "factors_keys": list(summary.get("factors_keys") or []),
        "tick_from": summary.get("tick_from"),
        "tick_to": summary.get("tick_to"),
        "count": int(summary.get("count") or 0),
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

    mapped_columns = {str(tick_col or "")}
    mapped_columns.update(str(col) for col in (resolved.get("factors") or {}).values() if col)
    mapped_columns.update(str(col) for col in (resolved.get("profiles") or {}).values() if col)
    unused_columns = [header for header in headers if header not in mapped_columns and header]

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

        factors_json: Dict[str, float] = {}
        for factor_key, source_column in (resolved.get("factors") or {}).items():
            value = _parse_float(row.get(source_column))
            if value is None:
                continue
            factors_json[_norm(factor_key)] = float(value)

        profiles_json: Dict[str, float] = {}
        for profile_key, source_column in (resolved.get("profiles") or {}).items():
            canonical = _canonical_profile_key(profile_key)
            if canonical is None:
                continue
            value = _parse_float(row.get(source_column))
            if value is None:
                continue
            if value < 0:
                warnings.append(f"Строка {row_idx}: профиль {canonical} < 0, обрезано до 0")
                value = 0.0
            profiles_json[canonical] = float(value)

        consumption: Dict[str, float] = {}
        for profile_key, legacy_load in _LEGACY_PROFILE_TO_LOAD.items():
            value = profiles_json.get(profile_key)
            if value is None:
                continue
            consumption[legacy_load] = float(value)

        extra: Dict[str, Any] = {}
        raw_columns: Dict[str, Any] = {}
        for key, value in row.items():
            parsed = _parse_float(value)
            norm_key = _norm(key)
            if parsed is not None:
                raw_columns[norm_key] = float(parsed)
            if key in mapped_columns:
                continue
            extra[norm_key] = parsed if parsed is not None else value
        extra["raw_columns"] = raw_columns

        periods.append(
            ForecastPeriod(
                tick=tick,
                illumination=factors_json.get("solar_factor"),
                wind=factors_json.get("wind_factor"),
                market_price=factors_json.get("market_price_buy"),
                consumption_json=consumption,
                factors_json=factors_json,
                profiles_json=profiles_json,
                extra_json=extra,
            )
        )

    if errors:
        raise ValueError("; ".join(errors))

    factors, profiles, ticks = _canonical_forecast_rows(periods)
    mapped_columns = _ordered_mapped_columns(headers=headers, resolved_map=resolved)
    mapped_headers = [str(row.get("raw_name")) for row in mapped_columns if row.get("raw_name")]
    compatibility_report = build_forecast_compatibility_report(
        session=session_obj,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        unused_columns=unused_columns,
        normalization_map=resolved,
    )
    is_compatible = bool(compatibility_report["is_compatible"])
    incompatibility_reason = "; ".join(compatibility_report.get("blocking_reasons") or [])

    forecast = Forecast(
        session_id=session_id,
        name=name,
        source_file=source_file,
        column_map_json=resolved,
        normalization_map_json={
            "resolved": resolved,
            "guessed": guessed,
            "unused_columns": unused_columns,
            "headers": list(headers),
            "mapped_headers": mapped_headers,
        },
        metadata_json={
            "rows": len(periods),
            "warnings": len(warnings),
            "warnings_detail": list(warnings),
            "factors_keys": sorted(factors.keys()),
            "profiles_keys": sorted(profiles.keys()),
        },
        compatibility_report_json=compatibility_report,
        is_compatible=is_compatible,
        incompatibility_reason=incompatibility_reason,
    )
    forecast.periods = periods
    db.session.add(forecast)
    db.session.commit()
    return forecast, ForecastDiagnostics(errors=[], warnings=warnings, column_map=resolved)


def build_forecast_pack(forecast: Forecast) -> Dict[str, Dict[str, Dict[int, float]]]:
    factors, profiles, _ = _canonical_forecast_rows(forecast.periods)

    wind = factors.get("wind_factor", {})
    solar = factors.get("solar_factor", {})
    market_buy = factors.get("market_price_buy", {})
    market_sell = factors.get("market_price_sell", {})
    balancing_penalty = factors.get("balancing_penalty_price", {})

    wind_bucket: Dict[str, Dict[int, float]] = {}
    if wind:
        wind_bucket["wind"] = wind
    for period in forecast.periods:
        raw_columns = dict((period.extra_json or {}).get("raw_columns") or {})
        for raw_key, raw_value in raw_columns.items():
            key = _norm(str(raw_key))
            if not key.startswith("wind_"):
                continue
            value = _parse_float(raw_value)
            if value is None:
                continue
            wind_bucket.setdefault(key, {})[int(period.tick)] = float(value)
    if "wind" not in wind_bucket and wind_bucket:
        ticks = sorted({int(tick) for series in wind_bucket.values() for tick in series.keys()})
        wind_bucket["wind"] = {
            int(tick): _mean(
                [float((series or {}).get(int(tick), 0.0) or 0.0) for series in wind_bucket.values() if series]
            )
            for tick in ticks
        }

    raw_load_series: Dict[str, Dict[int, float]] = {}
    for profile_key, legacy_key in _LEGACY_PROFILE_TO_LOAD.items():
        series = profiles.get(profile_key) or {}
        if not series:
            continue
        raw_load_series[legacy_key] = {int(tick): float(value) for tick, value in series.items()}

    load_series = _normalize_load_series(raw_load_series)
    if "class3" not in load_series:
        class3 = _build_class3_series(load_series)
        if class3:
            load_series["class3"] = class3
    load_bucket = _with_load_aliases(load_series)

    out: Dict[str, Dict[str, Dict[int, float]]] = {
        "wind": wind_bucket,
        "solar": {"solar": solar} if solar else {},
        "load": load_bucket,
        "market": {"price": market_buy} if market_buy else {},
    }
    if market_sell:
        out["market"]["sell_price"] = market_sell
    if balancing_penalty:
        out["market"]["balancing_penalty_price"] = balancing_penalty
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
        warnings = list((forecast.metadata_json or {}).get("warnings_detail") or [])
        summary = {
            "forecast_id": forecast.id,
            "source_kind": "selected_forecast",
            "name": forecast.name,
            "source_file": forecast.source_file,
            "count": 0,
            "tick_from": None,
            "tick_to": None,
            "avg_wind": None,
            "avg_illumination": None,
            "avg_market_price": None,
            "factors_keys": [],
            "profiles_keys": [],
            "load_series": [],
            "load_series_display": [],
            "load_series_raw": [],
            "raw_csv_columns": [],
            "used_raw_columns": [],
            "unsupported_raw_columns": [],
            "column_mapping_rows": [],
            "mapped_raw_columns": [],
            "mapped_raw_stats_display": [],
            "mapped_tick_range_label": format_tick_range(None, None, periods=0),
            "consumer_averages": {},
            "series_stats": {},
            "series_stats_display": [],
            "internal_canonical_load_series": [],
            "internal_canonical_series_stats": {},
            "column_display_labels": {},
            "resolved_column_map": {},
            "compatibility_report": dict(forecast.compatibility_report_json or {}),
            "object_coverage_rows": list(
                (forecast.compatibility_report_json or {}).get("rows") or []
            ),
            "quality": {
                "warnings": warnings,
                "problem_columns": ["wind_factor", "solar_factor", "market_price_buy"],
                "empty_columns": ["wind_factor", "solar_factor", "market_price_buy"],
                "text": f"Прогноз '{forecast.name}' пустой.",
            },
            "warnings": warnings,
            "text": f"Прогноз '{forecast.name}' пустой.",
        }
        summary["weather_analysis"] = _empty_weather_analysis(
            f"Прогноз '{forecast.name}' пустой, weather analysis недоступен."
        )
        return summary

    factors, profiles, ticks = _canonical_forecast_rows(periods)

    def avg(values: Iterable[Optional[float]]) -> Optional[float]:
        rows = [value for value in values if value is not None]
        if not rows:
            return None
        return round(sum(rows) / len(rows), 4)

    normalization_map = dict(forecast.normalization_map_json or {})
    resolved_map = dict(normalization_map.get("resolved") or forecast.column_map_json or {})
    display_labels = _display_labels_from_column_map(resolved_map)
    headers = list(normalization_map.get("headers") or [])
    mapped_columns = _ordered_mapped_columns(headers=headers, resolved_map=resolved_map)
    column_mapping_rows = _column_mapping_rows(mapped_columns)
    mapped_raw_columns = [
        str(row.get("raw_name")) for row in mapped_columns if str(row.get("raw_name") or "").strip()
    ]
    unsupported_raw_columns = [
        str(column)
        for column in normalization_map.get("unused_columns") or []
        if str(column).strip()
    ]

    load_rows: Dict[str, Dict[int, float]] = {}
    for profile_key, legacy_key in _LEGACY_PROFILE_TO_LOAD.items():
        if profile_key in profiles:
            load_rows[legacy_key] = dict(profiles[profile_key])
    if "class3" not in load_rows:
        class3 = _build_class3_series(load_rows)
        if class3:
            load_rows["class3"] = class3
    load_series_display = _build_mapped_load_series_display(
        mapped_columns=mapped_columns,
        profiles=profiles,
    )

    series_stats: Dict[str, Dict[str, float] | None] = {}
    for key in CANONICAL_FACTOR_KEYS:
        series_stats[key] = _series_stats((factors.get(key) or {}).values())
    for key in CANONICAL_PROFILE_KEYS:
        series_stats[key] = _series_stats((profiles.get(key) or {}).values())
    mapped_raw_stats_display = _build_mapped_raw_stats_display(
        mapped_columns=mapped_columns,
        factors=factors,
        profiles=profiles,
    )
    series_stats_display = list(mapped_raw_stats_display)

    warnings = list((forecast.metadata_json or {}).get("warnings_detail") or [])
    compatibility_report = dict(forecast.compatibility_report_json or {})
    text = (
        f"Периоды {ticks[0] if ticks else '—'}-{ticks[-1] if ticks else '—'}, "
        f"канонических профилей: {len(profiles)}."
    )
    if not bool(forecast.is_compatible):
        reason = forecast.incompatibility_reason or "совместимость прогноза неполная"
        text = f"{text} Блокировка оценки: {reason}."

    summary = {
        "forecast_id": forecast.id,
        "source_kind": "selected_forecast",
        "name": forecast.name,
        "source_file": forecast.source_file,
        "count": len(ticks),
        "tick_from": ticks[0] if ticks else None,
        "tick_to": ticks[-1] if ticks else None,
        "avg_wind": avg((factors.get("wind_factor") or {}).values()),
        "avg_illumination": avg((factors.get("solar_factor") or {}).values()),
        "avg_market_price": avg((factors.get("market_price_buy") or {}).values()),
        "factors_keys": sorted(factors.keys()),
        "profiles_keys": sorted(profiles.keys()),
        "load_series": [
            str(row.get("label") or row.get("key") or "") for row in load_series_display
        ],
        "load_series_display": load_series_display,
        "load_series_raw": list(mapped_raw_columns),
        "raw_csv_columns": [str(header) for header in headers if str(header).strip()],
        "used_raw_columns": list(mapped_raw_columns),
        "unsupported_raw_columns": list(unsupported_raw_columns),
        "column_mapping_rows": list(column_mapping_rows),
        "mapped_raw_columns": list(mapped_raw_columns),
        "mapped_raw_stats_display": list(mapped_raw_stats_display),
        "mapped_tick_range_label": format_tick_range(
            ticks[0] if ticks else None,
            ticks[-1] if ticks else None,
            periods=len(ticks),
        ),
        "consumer_averages": {key: avg(values.values()) for key, values in load_rows.items()},
        "series_stats": series_stats,
        "series_stats_display": series_stats_display,
        "internal_canonical_load_series": sorted(load_rows.keys()),
        "internal_canonical_series_stats": series_stats,
        "column_display_labels": display_labels,
        "resolved_column_map": resolved_map,
        "compatibility_report": compatibility_report,
        "object_coverage_rows": list(compatibility_report.get("rows") or []),
        "is_compatible": bool(forecast.is_compatible),
        "incompatibility_reason": forecast.incompatibility_reason or "",
        "quality": _quality_summary(
            headers=[*CANONICAL_FACTOR_KEYS, *CANONICAL_PROFILE_KEYS],
            warnings=warnings,
            series_stats=series_stats,
        ),
        "warnings": warnings,
        "text": text,
    }
    summary["weather_analysis"] = build_weather_analysis_from_periods(
        periods=periods,
        factors=factors,
        profiles=profiles,
        ticks=ticks,
    )
    return summary
