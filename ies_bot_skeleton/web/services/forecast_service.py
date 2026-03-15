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
    "fuel_price",
    "temperature",
    "time_of_day",
)
CANONICAL_PROFILE_KEYS = (
    "factory_load",
    "office_load",
    "house_load",
    "solar_profile",
    "wind_profile",
    "storage_default_profile",
)

CANONICAL_LOAD_KEYS = ("housea", "houseb", "office", "factory", "consumer", "load", "class3")
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
    "house_load": (
        "house_load",
        "house",
        "housea",
        "houseb",
        "load_house",
        "load_houseb",
        "load_housea",
        "consumption_houseb",
        "consumption_house",
    ),
    "solar_profile": ("solar_profile", "solar_output_profile"),
    "wind_profile": ("wind_profile", "wind_output_profile"),
    "storage_default_profile": ("storage_default_profile", "storage_profile"),
}

_LEGACY_PROFILE_TO_LOAD = {
    "house_load": "housea",
    "office_load": "office",
    "factory_load": "factory",
}

_LEGACY_LOAD_TO_PROFILE = {
    "house": "house_load",
    "housea": "house_load",
    "houseb": "house_load",
    "load_house": "house_load",
    "load_housea": "house_load",
    "load_houseb": "house_load",
    "consumption_house": "house_load",
    "consumption_houseb": "house_load",
    "office": "office_load",
    "load_office": "office_load",
    "consumption_office": "office_load",
    "factory": "factory_load",
    "load_factory": "factory_load",
    "consumption_factory": "factory_load",
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
        return "housea"
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
    if key == "housea":
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
        for key in ("housea", "houseb", "office", "consumer", "load"):
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

    # Backward compatibility with legacy upload payload.
    for factor_key, legacy_key in (
        ("wind_factor", "wind"),
        ("solar_factor", "illumination"),
        ("market_price_buy", "market_price"),
    ):
        raw = incoming_map.get(legacy_key)
        if raw:
            out["factors"][factor_key] = str(raw)
    legacy_consumption = incoming_map.get("consumption")
    if isinstance(legacy_consumption, dict):
        for profile_key, source_column in legacy_consumption.items():
            canonical = _canonical_profile_key(str(profile_key))
            if canonical and source_column:
                out["profiles"][canonical] = str(source_column)

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
            elif code == "office":
                required_profiles.append("office_load")
            else:
                required_profiles.append("house_load")
    elif resolved_role == "generator":
        if code == "wind" and "wind_factor" not in required_factors:
            required_factors.append("wind_factor")
        if (
            code in {"solar", "cyber_solar", "solarrobot"}
            and "solar_factor" not in required_factors
        ):
            required_factors.append("solar_factor")
        if model_type in {"wind_factor_curve", "solar_factor_output"}:
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
    wind_rows = (pack.get("wind", {}) or {}).get("wind", {}) or {}
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

    return {
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
        "quality": _quality_summary(
            headers=[*CANONICAL_FACTOR_KEYS, *CANONICAL_PROFILE_KEYS],
            warnings=[],
            series_stats=series_stats,
        ),
        "warnings": [],
        "text": "Используется встроенный прогноз тестовой игры.",
    }


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
        for key, value in row.items():
            if key in mapped_columns:
                continue
            parsed = _parse_float(value)
            extra[_norm(key)] = parsed if parsed is not None else value

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

    raw_load_series: Dict[str, Dict[int, float]] = {}
    for profile_key, legacy_key in _LEGACY_PROFILE_TO_LOAD.items():
        series = profiles.get(profile_key) or {}
        if not series:
            continue
        raw_load_series[legacy_key] = {int(tick): float(value) for tick, value in series.items()}

    # Backward fallback: merge legacy consumption rows if canonical profiles were absent.
    if not raw_load_series:
        for period in forecast.periods:
            for key, value in (period.consumption_json or {}).items():
                if value is None:
                    continue
                raw_load_series.setdefault(_norm(key), {})[int(period.tick)] = float(value)

    load_series = _normalize_load_series(raw_load_series)
    if "class3" not in load_series:
        class3 = _build_class3_series(load_series)
        if class3:
            load_series["class3"] = class3
    load_bucket = _with_load_aliases(load_series)

    out: Dict[str, Dict[str, Dict[int, float]]] = {
        "wind": {"wind": wind} if wind else {},
        "solar": {"solar": solar} if solar else {},
        "load": load_bucket,
        "market": {"price": market_buy} if market_buy else {},
    }
    if market_sell:
        out["market"]["sell_price"] = market_sell
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
        return {
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
            "quality": {
                "warnings": warnings,
                "problem_columns": ["wind_factor", "solar_factor", "market_price_buy"],
                "empty_columns": ["wind_factor", "solar_factor", "market_price_buy"],
                "text": f"Прогноз '{forecast.name}' пустой.",
            },
            "warnings": warnings,
            "text": f"Прогноз '{forecast.name}' пустой.",
        }

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
    mapped_raw_columns = [
        str(row.get("raw_name"))
        for row in mapped_columns
        if str(row.get("raw_name") or "").strip()
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

    return {
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
        "load_series": [str(row.get("label") or row.get("key") or "") for row in load_series_display],
        "load_series_display": load_series_display,
        "load_series_raw": list(mapped_raw_columns),
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
