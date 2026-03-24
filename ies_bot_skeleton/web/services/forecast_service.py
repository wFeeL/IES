from __future__ import annotations

import csv
import io
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..extensions import db
from ..models import Forecast, ForecastPeriod, GameSession

CANONICAL_FACTOR_KEYS = (
    "wind_factor",
    "solar_factor",
    "market_price_buy",
    "market_price_sell",
    "balancing_penalty_price",
)
CANONICAL_PROFILE_KEYS = (
    "factory_load",
    "office_load",
    "house_a_load",
    "house_b_load",
    "hospital_load",
)
PROFILE_FROM_LOAD = {
    "factory": "factory_load",
    "office": "office_load",
    "house_a": "house_a_load",
    "house_b": "house_b_load",
    "hospital": "hospital_load",
}
LOAD_FROM_PROFILE = {value: key for key, value in PROFILE_FROM_LOAD.items()}


class ForecastParseError(ValueError):
    pass


class ForecastDiagnostics:
    def __init__(self, *, errors: List[str], warnings: List[str], column_map: Dict[str, Any]) -> None:
        self.errors = list(errors)
        self.warnings = list(warnings)
        self.column_map = dict(column_map)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "column_map": dict(self.column_map),
        }



def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")



def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    if raw == "":
        return None
    try:
        out = float(raw)
    except Exception:
        return None
    if out != out:
        return None
    return out



def _detect_delimiter(sample: str) -> str:
    variants = [",", ";", "\t", "|"]
    return max(variants, key=sample.count)



def _read_csv(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    text = content.decode("utf-8", errors="replace")
    delimiter = _detect_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
    return headers, rows



def _canonical_profile_key(raw_key: str) -> Optional[str]:
    key = _norm(raw_key)
    if key in CANONICAL_PROFILE_KEYS:
        return key
    mapping = {
        "factory": "factory_load",
        "office": "office_load",
        "house": "house_a_load",
        "housea": "house_a_load",
        "house_a": "house_a_load",
        "houseb": "house_b_load",
        "house_b": "house_b_load",
        "hospital": "hospital_load",
        "load_factory": "factory_load",
        "load_office": "office_load",
        "load_house": "house_a_load",
        "load_housea": "house_a_load",
        "load_house_a": "house_a_load",
        "load_houseb": "house_b_load",
        "load_house_b": "house_b_load",
        "load_hospital": "hospital_load",
    }
    return mapping.get(key)



def _guess_columns(headers: Iterable[str]) -> Dict[str, Any]:
    guessed: Dict[str, Any] = {"tick": None, "factors": {}, "profiles": {}}
    normalized = {_norm(header): header for header in headers}
    guessed["tick"] = normalized.get("tick") or normalized.get("time") or normalized.get("t")
    for canonical, aliases in {
        "wind_factor": ["wind_factor", "wind", "wind_main", "wind_speed"],
        "solar_factor": ["solar_factor", "illumination", "solar", "sun"],
        "market_price_buy": ["market_price_buy", "market_price", "price_buy", "price"],
        "market_price_sell": ["market_price_sell", "sell_price", "price_sell"],
        "balancing_penalty_price": ["balancing_penalty_price", "balancing_penalty", "imbalance_penalty"],
    }.items():
        for alias in aliases:
            if _norm(alias) in normalized:
                guessed["factors"][canonical] = normalized[_norm(alias)]
                break
    for header in headers:
        profile_key = _canonical_profile_key(header)
        if profile_key:
            guessed["profiles"][profile_key] = header
    return guessed



def _resolved_column_map(guessed: Dict[str, Any], incoming_map: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    resolved = {
        "tick": guessed.get("tick"),
        "factors": dict(guessed.get("factors") or {}),
        "profiles": dict(guessed.get("profiles") or {}),
    }
    if not incoming_map:
        return resolved
    if incoming_map.get("tick"):
        resolved["tick"] = incoming_map["tick"]
    for key, value in dict(incoming_map.get("factors") or {}).items():
        if _norm(key) in CANONICAL_FACTOR_KEYS and value:
            resolved["factors"][_norm(key)] = str(value)
    for key, value in dict(incoming_map.get("profiles") or {}).items():
        canonical = _canonical_profile_key(str(key))
        if canonical and value:
            resolved["profiles"][canonical] = str(value)
    return resolved



def _canonical_rows(periods: Iterable[ForecastPeriod]) -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    ticks: List[int] = []
    for period in periods:
        tick = int(period.tick)
        ticks.append(tick)
        for key, value in dict(period.factors_json or {}).items():
            parsed = _parse_float(value)
            if parsed is None:
                continue
            factors.setdefault(_norm(key), {})[tick] = float(parsed)
        for key, value in dict(period.profiles_json or {}).items():
            profile_key = _canonical_profile_key(str(key))
            parsed = _parse_float(value)
            if profile_key is None or parsed is None:
                continue
            profiles.setdefault(profile_key, {})[tick] = float(parsed)
    return factors, profiles, sorted(set(ticks))



def _empty_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    return {"wind": {}, "solar": {}, "load": {}, "market": {}}



def load_bundled_forecast_pack() -> Dict[str, Dict[str, Dict[int, float]]]:
    # Боевой режим: встроенный прогноз полностью отключён.
    return _empty_pack()



def is_forecast_pack_empty(pack: Optional[Dict[str, Dict[str, Dict[int, float]]]]) -> bool:
    if not isinstance(pack, dict):
        return True
    for bucket in pack.values():
        if isinstance(bucket, dict) and any(bucket.values()):
            return False
    return True



def parse_and_store_forecast(
    *,
    session_id: int,
    name: str,
    source_file: str,
    content: bytes,
    column_map: Optional[Dict[str, Any]] = None,
) -> Tuple[Forecast, ForecastDiagnostics]:
    session = db.session.get(GameSession, session_id)
    if session is None:
        raise ForecastParseError(f"Session {session_id} not found")

    headers, rows = _read_csv(content)
    guessed = _guess_columns(headers)
    resolved = _resolved_column_map(guessed, column_map)
    diagnostics_errors: List[str] = []
    diagnostics_warnings: List[str] = []

    tick_col = resolved.get("tick")
    if not tick_col:
        diagnostics_errors.append("Не найден столбец tick")
    if not rows:
        diagnostics_errors.append("CSV пустой")
    if diagnostics_errors:
        raise ForecastParseError("; ".join(diagnostics_errors))

    periods: List[ForecastPeriod] = []
    for row_idx, row in enumerate(rows, start=2):
        tick_raw = row.get(tick_col)
        tick = _parse_float(tick_raw)
        if tick is None:
            diagnostics_errors.append(f"Строка {row_idx}: некорректный tick '{tick_raw}'")
            continue
        factors_json: Dict[str, float] = {}
        profiles_json: Dict[str, float] = {}
        for key, source in dict(resolved.get("factors") or {}).items():
            parsed = _parse_float(row.get(source))
            if parsed is not None:
                factors_json[_norm(key)] = float(parsed)
        for key, source in dict(resolved.get("profiles") or {}).items():
            parsed = _parse_float(row.get(source))
            if parsed is not None:
                profiles_json[key] = max(0.0, float(parsed))
        periods.append(
            ForecastPeriod(
                tick=int(tick),
                illumination=factors_json.get("solar_factor"),
                wind=factors_json.get("wind_factor"),
                market_price=factors_json.get("market_price_buy"),
                consumption_json={LOAD_FROM_PROFILE[k]: v for k, v in profiles_json.items() if k in LOAD_FROM_PROFILE},
                factors_json=factors_json,
                profiles_json=profiles_json,
                extra_json={"raw_headers": list(headers)},
            )
        )

    if diagnostics_errors:
        raise ForecastParseError("; ".join(diagnostics_errors))

    factors, profiles, ticks = _canonical_rows(periods)
    compatibility = build_forecast_compatibility_report(
        session=session,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        unused_columns=[header for header in headers if header not in {tick_col, *resolved["factors"].values(), *resolved["profiles"].values()}],
        normalization_map=resolved,
    )
    forecast = Forecast(
        session_id=session_id,
        name=name,
        source_file=source_file,
        column_map_json=resolved,
        normalization_map_json={"resolved": resolved, "headers": list(headers)},
        metadata_json={"rows": len(periods), "warnings_detail": diagnostics_warnings},
        compatibility_report_json=compatibility,
        is_compatible=bool(compatibility.get("is_compatible")),
        incompatibility_reason="; ".join(list(compatibility.get("blocking_reasons") or [])),
    )
    forecast.periods = periods
    db.session.add(forecast)
    db.session.commit()
    return forecast, ForecastDiagnostics(errors=[], warnings=diagnostics_warnings, column_map=resolved)



def build_forecast_pack(forecast: Forecast) -> Dict[str, Dict[str, Dict[int, float]]]:
    factors, profiles, _ = _canonical_rows(list(forecast.periods))
    wind_bucket: Dict[str, Dict[int, float]] = {}
    if factors.get("wind_factor"):
        wind_bucket["wind"] = dict(factors["wind_factor"])
    load_bucket: Dict[str, Dict[int, float]] = {}
    for profile_key, load_key in LOAD_FROM_PROFILE.items():
        if profiles.get(profile_key):
            load_bucket[load_key] = dict(profiles[profile_key])
    return {
        "wind": wind_bucket,
        "solar": {"solar": dict(factors.get("solar_factor") or {})} if factors.get("solar_factor") else {},
        "load": load_bucket,
        "market": {
            key: dict(values)
            for key, values in {
                "price": factors.get("market_price_buy") or {},
                "sell_price": factors.get("market_price_sell") or {},
                "balancing_penalty_price": factors.get("balancing_penalty_price") or {},
            }.items()
            if values
        },
    }



def _basic_series_stats(series: Dict[int, float]) -> Optional[Dict[str, float]]:
    if not series:
        return None
    values = list(series.values())
    return {
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "avg": round(sum(values) / len(values), 4),
    }



def summarize_forecast(forecast: Forecast) -> Dict[str, Any]:
    factors, profiles, ticks = _canonical_rows(list(forecast.periods))
    compatibility = dict(forecast.compatibility_report_json or {})
    return {
        "forecast_id": forecast.id,
        "source_kind": "selected_forecast",
        "name": forecast.name,
        "source_file": forecast.source_file,
        "count": len(ticks),
        "tick_from": ticks[0] if ticks else None,
        "tick_to": ticks[-1] if ticks else None,
        "avg_wind": _basic_series_stats(factors.get("wind_factor") or {}) and _basic_series_stats(factors.get("wind_factor") or {})["avg"],
        "avg_illumination": _basic_series_stats(factors.get("solar_factor") or {}) and _basic_series_stats(factors.get("solar_factor") or {})["avg"],
        "avg_market_price": _basic_series_stats(factors.get("market_price_buy") or {}) and _basic_series_stats(factors.get("market_price_buy") or {})["avg"],
        "factors_keys": sorted(factors.keys()),
        "profiles_keys": sorted(profiles.keys()),
        "load_series": [LOAD_FROM_PROFILE[key] for key in sorted(profiles.keys()) if key in LOAD_FROM_PROFILE],
        "load_series_display": [
            {"key": LOAD_FROM_PROFILE[key], "label": LOAD_FROM_PROFILE[key], "avg": _basic_series_stats(values)["avg"] if _basic_series_stats(values) else None, "is_service": False, "is_raw_label": False}
            for key, values in sorted(profiles.items()) if key in LOAD_FROM_PROFILE
        ],
        "load_series_raw": [],
        "raw_csv_columns": list((forecast.normalization_map_json or {}).get("headers") or []),
        "used_raw_columns": [
            value for value in [
                (forecast.column_map_json or {}).get("tick"),
                *((forecast.column_map_json or {}).get("factors") or {}).values(),
                *((forecast.column_map_json or {}).get("profiles") or {}).values(),
            ] if value
        ],
        "unsupported_raw_columns": [],
        "column_mapping_rows": [],
        "mapped_raw_columns": [],
        "mapped_raw_stats_display": [],
        "mapped_tick_range_label": f"{ticks[0]}-{ticks[-1]}" if ticks else "—",
        "consumer_averages": {LOAD_FROM_PROFILE[k]: _basic_series_stats(v)["avg"] for k, v in profiles.items() if k in LOAD_FROM_PROFILE and _basic_series_stats(v)},
        "series_stats": {key: _basic_series_stats(values) for key, values in {**factors, **profiles}.items()},
        "series_stats_display": [],
        "internal_canonical_load_series": [LOAD_FROM_PROFILE[k] for k in profiles.keys() if k in LOAD_FROM_PROFILE],
        "internal_canonical_series_stats": {key: _basic_series_stats(values) for key, values in {**factors, **profiles}.items()},
        "column_display_labels": {},
        "resolved_column_map": dict(forecast.column_map_json or {}),
        "compatibility_report": compatibility,
        "object_coverage_rows": list(compatibility.get("rows") or []),
        "is_compatible": bool(forecast.is_compatible),
        "incompatibility_reason": forecast.incompatibility_reason or "",
        "quality": {
            "warnings": list((forecast.metadata_json or {}).get("warnings_detail") or []),
            "problem_columns": [],
            "empty_columns": [],
            "text": "Прогноз загружен." if forecast.is_compatible else (forecast.incompatibility_reason or "Прогноз несовместим."),
        },
        "warnings": list((forecast.metadata_json or {}).get("warnings_detail") or []),
        "text": "Пользовательский прогноз загружен.",
        "weather_analysis": {
            "mode": "user_forecast",
            "availability": {
                "has_wind_range": False,
                "has_solar_east_west": False,
                "has_category_breakdown": bool(profiles),
            },
            "kpis": {},
            "series": {"tick": ticks},
            "charts": {},
            "decision_support": {"headline": "Используйте этот прогноз для оценки лотов.", "cards": []},
            "tables": {"main_stats": [], "generator_stats": []},
            "insights": [],
        },
    }



def summarize_forecast_for_session(*, session: GameSession, forecast: Forecast) -> Dict[str, Any]:
    summary = summarize_forecast(forecast)
    factors, profiles, ticks = _canonical_rows(list(forecast.periods))
    compatibility = build_forecast_compatibility_report(
        session=session,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        unused_columns=[],
        normalization_map=dict(forecast.column_map_json or {}),
    )
    summary["compatibility_report"] = compatibility
    summary["is_compatible"] = bool(compatibility.get("is_compatible"))
    summary["incompatibility_reason"] = "; ".join(list(compatibility.get("blocking_reasons") or []))
    return summary



def bundled_forecast_summary() -> Dict[str, Any]:
    return {
        "forecast_id": None,
        "source_kind": "removed_bundled_forecast",
        "name": "Встроенный прогноз отключён",
        "source_file": "",
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
        "mapped_tick_range_label": "—",
        "consumer_averages": {},
        "series_stats": {},
        "series_stats_display": [],
        "internal_canonical_load_series": [],
        "internal_canonical_series_stats": {},
        "column_display_labels": {},
        "resolved_column_map": {},
        "compatibility_report": {
            "is_compatible": False,
            "blocking_reasons": ["Встроенный прогноз удалён из боевого режима 2026."],
            "covered_types": [],
            "partial_types": [],
            "missing_types": [],
            "rows": [],
            "unused_columns": [],
            "normalization_map": {},
        },
        "object_coverage_rows": [],
        "is_compatible": False,
        "incompatibility_reason": "Встроенный прогноз удалён из боевого режима 2026.",
        "quality": {
            "warnings": ["Встроенный прогноз отключён."],
            "problem_columns": [],
            "empty_columns": [],
            "text": "Встроенный прогноз отключён. Используйте пользовательский CSV.",
        },
        "warnings": ["Встроенный прогноз отключён."],
        "text": "Встроенный прогноз отключён. Используйте пользовательский CSV.",
        "weather_analysis": {"mode": "removed"},
    }



def bundled_forecast_summary_for_session(session: GameSession) -> Dict[str, Any]:
    del session
    return bundled_forecast_summary()



def build_forecast_compatibility_report(
    *,
    session: GameSession,
    ticks: List[int],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    unused_columns: List[str],
    normalization_map: Dict[str, Any],
) -> Dict[str, Any]:
    horizon = int(((session.ruleset.config_json or {}).get("time", {}) or {}).get("horizon_ticks", 48) or 48)
    rows: List[Dict[str, Any]] = []
    blocking_reasons: List[str] = []
    if len(ticks) != horizon:
        blocking_reasons.append(f"Горизонт прогноза {len(ticks)} тактов не совпадает с ruleset ({horizon}).")

    used_codes: List[str] = []
    for obj in session.objects:
        if obj.is_active and obj.object_type is not None:
            used_codes.append(_norm(obj.object_type.code))
    for lot in session.lots:
        for item in lot.items:
            if item.object_type is not None:
                used_codes.append(_norm(item.object_type.code))

    required_profiles: Dict[str, List[str]] = {
        "factory": ["factory_load"],
        "office": ["office_load"],
        "house_a": ["house_a_load"],
        "house_b": ["house_b_load"],
        "hospital": ["hospital_load"],
    }
    required_factors: Dict[str, List[str]] = {
        "wind": ["wind_factor"],
        "solar": ["solar_factor"],
        "storage": [],
        "main_substation": [],
        "mini_substation": [],
    }
    covered_types: List[str] = []
    missing_types: List[str] = []
    partial_types: List[str] = []

    for code in sorted(set(used_codes)):
        need_profiles = list(required_profiles.get(code, []))
        need_factors = list(required_factors.get(code, []))
        missing_profiles = [key for key in need_profiles if not profiles.get(key)]
        missing_factors = [key for key in need_factors if not factors.get(key)]
        if missing_profiles or missing_factors:
            status = "missing"
            missing_types.append(code)
        else:
            status = "covered"
            covered_types.append(code)
        rows.append(
            {
                "object_type_code": code,
                "object_type_name": code,
                "status": status,
                "required_profiles": need_profiles,
                "required_factors": need_factors,
                "optional_profiles": [],
                "optional_factors": [],
                "missing_profiles": missing_profiles,
                "missing_factors": missing_factors,
                "partial_profiles": [],
                "partial_factors": [],
                "optional_profiles_present": [],
                "optional_factors_present": [],
            }
        )
    if missing_types:
        blocking_reasons.append("Нет полного покрытия прогноза для типов объектов: " + ", ".join(sorted(set(missing_types))))
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



def session_forecast_compatibility(*, session: GameSession, forecast: Optional[Forecast]) -> Dict[str, Any]:
    if forecast is None:
        summary = bundled_forecast_summary_for_session(session)
    else:
        summary = summarize_forecast_for_session(session=session, forecast=forecast)
    return {
        "session_id": int(session.id),
        "forecast_id": int(forecast.id) if forecast is not None else None,
        "forecast_name": summary.get("name") or "Прогноз не выбран",
        "is_compatible": bool(summary.get("is_compatible", False)),
        "incompatibility_reason": summary.get("incompatibility_reason") or "",
        "compatibility_report": dict(summary.get("compatibility_report") or {}),
        "profiles_keys": list(summary.get("profiles_keys") or []),
        "factors_keys": list(summary.get("factors_keys") or []),
        "tick_from": summary.get("tick_from"),
        "tick_to": summary.get("tick_to"),
        "count": int(summary.get("count") or 0),
    }
