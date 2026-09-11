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

FACTOR_ALIASES = {
    "wind_factor": (
        "wind_factor",
        "wind",
        "wind_main",
        "wind_speed",
        "ветер",
        "скорость_ветра",
        "скоростьветра",
    ),
    "solar_factor": (
        "solar_factor",
        "illumination",
        "solar",
        "sun",
        "солнце",
        "освещенность",
        "освещённость",
        "инсоляция",
    ),
    "market_price_buy": (
        "market_price_buy",
        "market_price",
        "price_buy",
        "price",
        "цена_покупки",
        "ценапокупки",
        "рыночная_цена",
        "рыночнаяцена",
        "цена_рынка",
        "ценарынка",
    ),
    "market_price_sell": (
        "market_price_sell",
        "sell_price",
        "price_sell",
        "цена_продажи",
        "ценапродажи",
    ),
    "balancing_penalty_price": (
        "balancing_penalty_price",
        "balancing_penalty",
        "imbalance_penalty",
        "штраф_балансировки",
        "штрафбалансировки",
        "штраф_дисбаланса",
        "штрафдисбаланса",
    ),
}


class ForecastParseError(ValueError):
    pass


class ForecastDiagnostics:
    def __init__(
        self, *, errors: List[str], warnings: List[str], column_map: Dict[str, Any]
    ) -> None:
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
    rows = [
        dict(row)
        for row in reader
        if any(str(value or "").strip() for value in (row or {}).values())
    ]
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
        "factoryload": "factory_load",
        "officeload": "office_load",
        "houseaload": "house_a_load",
        "housebload": "house_b_load",
        "hospitalload": "hospital_load",
        "factorys": "factory_load",
        "offices": "office_load",
        "hospitals": "hospital_load",
        "завод": "factory_load",
        "заводы": "factory_load",
        "фабрика": "factory_load",
        "фабрики": "factory_load",
        "офис": "office_load",
        "офисы": "office_load",
        "дом": "house_a_load",
        "дома": "house_a_load",
        "домаа": "house_a_load",
        "домаa": "house_a_load",
        "дом_a": "house_a_load",
        "houseа": "house_a_load",
        "домаб": "house_b_load",
        "домаb": "house_b_load",
        "дом_b": "house_b_load",
        "houseб": "house_b_load",
        "больница": "hospital_load",
        "больницы": "hospital_load",
    }
    return mapping.get(key)


def _guess_columns(headers: Iterable[str]) -> Dict[str, Any]:
    guessed: Dict[str, Any] = {"tick": None, "factors": {}, "profiles": {}}
    normalized = {_norm(header): header for header in headers}
    guessed["tick"] = (
        normalized.get("tick")
        or normalized.get("time")
        or normalized.get("t")
        or normalized.get("такт")
        or normalized.get("время")
    )
    for canonical, aliases in FACTOR_ALIASES.items():
        for alias in aliases:
            if _norm(alias) in normalized:
                guessed["factors"][canonical] = normalized[_norm(alias)]
                break
    for header in headers:
        profile_key = _canonical_profile_key(header)
        if profile_key:
            guessed["profiles"][profile_key] = header
    return guessed


def _resolved_column_map(
    guessed: Dict[str, Any], incoming_map: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
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


def _canonical_rows(
    periods: Iterable[ForecastPeriod],
) -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
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
    ruleset_cfg = dict((session.ruleset.config_json or {}) if session.ruleset is not None else {})
    horizon = int(((ruleset_cfg.get("time") or {}).get("horizon_ticks", 48)) or 48)

    tick_col = resolved.get("tick")
    if not rows:
        diagnostics_errors.append("CSV пустой")
    synthetic_tick = not bool(tick_col)
    rows_for_parse = list(rows)
    if synthetic_tick and rows_for_parse:
        if len(rows_for_parse) > horizon:
            diagnostics_warnings.append(
                f"Столбец tick не найден; использованы первые {horizon} строк из {len(rows_for_parse)}."
            )
            rows_for_parse = rows_for_parse[:horizon]
        else:
            diagnostics_warnings.append(
                f"Столбец tick не найден; использованы порядковые номера строк 0..{len(rows_for_parse) - 1}."
            )
    if diagnostics_errors:
        raise ForecastParseError("; ".join(diagnostics_errors))

    periods: List[ForecastPeriod] = []
    for row_idx, row in enumerate(rows_for_parse, start=2):
        if synthetic_tick:
            tick = float(row_idx - 2)
        else:
            tick_raw = row.get(tick_col)
            tick = _parse_float(tick_raw)
            if tick is None:
                diagnostics_errors.append(f"Строка {row_idx}: некорректный tick '{tick_raw}'")
                continue
        factors_json: Dict[str, float] = {}
        profiles_json: Dict[str, float] = {}
        # Every numeric column is kept verbatim so the weather analysis can read
        # wind_from/wind_to and sun_east/sun_west, which have no canonical slot.
        raw_columns: Dict[str, float] = {}
        for key, value in row.items():
            parsed = _parse_float(value)
            if parsed is not None:
                raw_columns[_norm(str(key))] = float(parsed)
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
                consumption_json={
                    LOAD_FROM_PROFILE[k]: v
                    for k, v in profiles_json.items()
                    if k in LOAD_FROM_PROFILE
                },
                factors_json=factors_json,
                profiles_json=profiles_json,
                extra_json={"raw_headers": list(headers), "raw_columns": raw_columns},
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
        unused_columns=[
            header
            for header in headers
            if header
            not in {tick_col, *resolved["factors"].values(), *resolved["profiles"].values()}
        ],
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
    return forecast, ForecastDiagnostics(
        errors=[], warnings=diagnostics_warnings, column_map=resolved
    )


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
        "solar": (
            {"solar": dict(factors.get("solar_factor") or {})}
            if factors.get("solar_factor")
            else {}
        ),
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


def _column_mapping_rows(forecast: Forecast) -> List[Dict[str, Any]]:
    """Which raw CSV column became which canonical series."""
    column_map = forecast.column_map_json or {}
    pairs: List[tuple] = []
    if column_map.get("tick"):
        pairs.append(("tick", column_map["tick"]))
    for group in ("factors", "profiles"):
        for canonical_key, raw_name in (column_map.get(group) or {}).items():
            if raw_name:
                pairs.append((canonical_key, raw_name))
    return [
        {"raw_name": str(raw_name), "canonical_key": str(canonical_key)}
        for canonical_key, raw_name in pairs
    ]


def _unsupported_raw_columns(forecast: Forecast) -> List[str]:
    """Headers the parser saw but could not place."""
    headers = list((forecast.normalization_map_json or {}).get("headers") or [])
    mapped = {row["raw_name"] for row in _column_mapping_rows(forecast)}
    return [str(header) for header in headers if str(header) not in mapped]


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
        "avg_wind": _basic_series_stats(factors.get("wind_factor") or {})
        and _basic_series_stats(factors.get("wind_factor") or {})["avg"],
        "avg_illumination": _basic_series_stats(factors.get("solar_factor") or {})
        and _basic_series_stats(factors.get("solar_factor") or {})["avg"],
        "avg_market_price": _basic_series_stats(factors.get("market_price_buy") or {})
        and _basic_series_stats(factors.get("market_price_buy") or {})["avg"],
        "factors_keys": sorted(factors.keys()),
        "profiles_keys": sorted(profiles.keys()),
        "load_series": [
            LOAD_FROM_PROFILE[key] for key in sorted(profiles.keys()) if key in LOAD_FROM_PROFILE
        ],
        "load_series_display": [
            {
                "key": LOAD_FROM_PROFILE[key],
                "label": LOAD_FROM_PROFILE[key],
                "avg": _basic_series_stats(values)["avg"] if _basic_series_stats(values) else None,
                "is_service": False,
                "is_raw_label": False,
            }
            for key, values in sorted(profiles.items())
            if key in LOAD_FROM_PROFILE
        ],
        "load_series_raw": [],
        "raw_csv_columns": list((forecast.normalization_map_json or {}).get("headers") or []),
        "used_raw_columns": [
            value
            for value in [
                (forecast.column_map_json or {}).get("tick"),
                *((forecast.column_map_json or {}).get("factors") or {}).values(),
                *((forecast.column_map_json or {}).get("profiles") or {}).values(),
            ]
            if value
        ],
        "unsupported_raw_columns": _unsupported_raw_columns(forecast),
        "column_mapping_rows": _column_mapping_rows(forecast),
        "mapped_raw_columns": [row["raw_name"] for row in _column_mapping_rows(forecast)],
        "mapped_raw_stats_display": [],
        "mapped_tick_range_label": f"{ticks[0]}-{ticks[-1]}" if ticks else "—",
        "consumer_averages": {
            LOAD_FROM_PROFILE[k]: _basic_series_stats(v)["avg"]
            for k, v in profiles.items()
            if k in LOAD_FROM_PROFILE and _basic_series_stats(v)
        },
        "series_stats": {
            key: _basic_series_stats(values) for key, values in {**factors, **profiles}.items()
        },
        "series_stats_display": [],
        "internal_canonical_load_series": [
            LOAD_FROM_PROFILE[k] for k in profiles.keys() if k in LOAD_FROM_PROFILE
        ],
        "internal_canonical_series_stats": {
            key: _basic_series_stats(values) for key, values in {**factors, **profiles}.items()
        },
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
            "text": (
                "Прогноз загружен."
                if forecast.is_compatible
                else (forecast.incompatibility_reason or "Прогноз несовместим.")
            ),
        },
        "warnings": list((forecast.metadata_json or {}).get("warnings_detail") or []),
        "text": "Пользовательский прогноз загружен.",
        "weather_analysis": build_weather_analysis_from_periods(
            periods=list(forecast.periods),
            factors=factors,
            profiles=profiles,
            ticks=ticks,
        ),
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
    horizon = int(
        ((session.ruleset.config_json or {}).get("time", {}) or {}).get("horizon_ticks", 48) or 48
    )
    rows: List[Dict[str, Any]] = []
    blocking_reasons: List[str] = []
    if len(ticks) != horizon:
        blocking_reasons.append(
            f"Горизонт прогноза {len(ticks)} тактов не совпадает с ruleset ({horizon})."
        )

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
        blocking_reasons.append(
            "Нет полного покрытия прогноза для типов объектов: "
            + ", ".join(sorted(set(missing_types)))
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


def session_forecast_compatibility(
    *, session: GameSession, forecast: Optional[Forecast]
) -> Dict[str, Any]:
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


def _empty_weather_analysis(
    message: str = "Недостаточно данных для анализа прогноза.",
) -> Dict[str, Any]:
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


def _weather_raw_series_from_periods(
    periods: Iterable[ForecastPeriod],
) -> Dict[str, Dict[int, float]]:
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


def _compute_solar_from_single_factor(
    ticks: List[int], solar_factor: Dict[int, float]
) -> Dict[str, Dict[int, float]]:
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


def _compute_wind_from_average(
    ticks: List[int], wind_avg_source: Dict[int, float]
) -> Dict[str, Dict[int, float]]:
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
        generation = _sum_available(
            _series_value(solar_improved, tick), _series_value(wind_gen, tick)
        )
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


def _build_weather_tables(
    series_payload: Dict[str, Any], kpis: Dict[str, Any]
) -> Dict[str, List[Dict[str, Any]]]:
    main_stats: List[Dict[str, Any]] = []
    generator_stats: List[Dict[str, Any]] = []

    for key in ("total_consumption", "total_generation", "balance", "wind_avg", "solar_improved"):
        row = _build_weather_table_row(
            key, WEATHER_SERIES_LABELS[key], series_payload.get(key) or []
        )
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
    category_has_values = any(
        _weather_chart_available(values) for values in category_consumption.values()
    )
    has_generation = _weather_chart_available(series_payload.get("total_generation"))
    has_consumption = _weather_chart_available(series_payload.get("total_consumption"))
    has_balance = _weather_chart_available(series_payload.get("balance"))
    has_balance_min = _weather_chart_available(series_payload.get("balance_min"))
    has_balance_max = _weather_chart_available(series_payload.get("balance_max"))
    has_wind = _weather_chart_available(series_payload.get("wind_avg"))
    has_solar_mix = _weather_chart_available(
        series_payload.get("solar_improved")
    ) or _weather_chart_available(series_payload.get("wind_gen"))

    return {
        "generation_vs_consumption": {
            "title": "Генерация и потребление",
            "available": has_generation and has_consumption,
            "reason": (
                None
                if has_generation and has_consumption
                else "Недостаточно рядов для одновременного сравнения генерации и потребления."
            ),
            "priority": "primary",
        },
        "balance_uncertainty": {
            "title": "Баланс с коридором неопределённости",
            "available": has_balance and has_balance_min and has_balance_max,
            "reason": (
                None
                if has_balance and has_balance_min and has_balance_max
                else "Не удалось рассчитать полный коридор неопределённости."
            ),
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
            "reason": (
                None
                if category_has_values
                else "В прогнозе нет разбивки потребления по категориям."
            ),
            "priority": "primary",
        },
        "solar_models": {
            "title": "Солнечная генерация: simple vs improved",
            "available": availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("solar_simple"))
            and _weather_chart_available(series_payload.get("solar_improved")),
            "reason": (
                None
                if availability.get("has_solar_east_west", False)
                and _weather_chart_available(series_payload.get("solar_simple"))
                and _weather_chart_available(series_payload.get("solar_improved"))
                else "Нужны отдельные ряды sun_east и sun_west."
            ),
            "priority": "secondary",
        },
        "solar_activity": {
            "title": "Солнечная активность east/west",
            "available": availability.get("has_solar_east_west", False)
            and _weather_chart_available(series_payload.get("sun_east"))
            and _weather_chart_available(series_payload.get("sun_west")),
            "reason": (
                None
                if availability.get("has_solar_east_west", False)
                and _weather_chart_available(series_payload.get("sun_east"))
                and _weather_chart_available(series_payload.get("sun_west"))
                else "Для этого графика нужны отдельные ряды east/west."
            ),
            "priority": "secondary",
        },
        "generation_types": {
            "title": "Сравнение источников генерации",
            "available": has_solar_mix,
            "reason": (
                None
                if has_solar_mix
                else "Недостаточно данных по солнечной или ветровой генерации."
            ),
            "priority": "secondary",
        },
        "source_mix": {
            "title": "Структура генерации и потребление",
            "available": has_consumption and has_solar_mix,
            "reason": (
                None
                if has_consumption and has_solar_mix
                else "Нужны и потребление, и хотя бы один источник генерации."
            ),
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
        if value is not None
        and ((sign == "deficit" and value < 0) or (sign == "surplus" and value > 0))
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
        headline = (
            "Баланс близок к нейтральному: лучше смотреть на устойчивость и качество профиля."
        )

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
        caution_parts.append(
            "прогноз неполный, поэтому покупать узкоспециализированные лоты стоит осторожнее"
        )
    if not caution_parts:
        caution_parts.append(
            "не видно явного провала по источникам, но перегружать портфель одним типом генерации не стоит"
        )

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
            {
                "tone": "timing",
                "title": "По каким тактам смотреть",
                "text": "; ".join(timing_parts) + ".",
            },
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
        insights.append(
            "Графики east/west отключены, потому что прогноз не содержит отдельных рядов sun_east и sun_west."
        )

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
    if (
        generation_max is not None
        and consumption_max is not None
        and consumption_max > generation_max
    ):
        insights.append("Пик потребления заметно выше пиковой генерации.")

    if not availability.get("has_category_breakdown"):
        insights.append(
            "Детальная разбивка потребления по категориям недоступна для этого формата прогноза."
        )

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
    canonical_house_series = _sum_series_dicts(
        {int(tick): float(value) for tick, value in (profiles.get("house_load") or {}).items()},
        {int(tick): float(value) for tick, value in (profiles.get("house_a_load") or {}).items()},
        {int(tick): float(value) for tick, value in (profiles.get("house_b_load") or {}).items()},
    )
    canonical_category_series = {
        "hospital": {
            int(tick): float(value) for tick, value in (profiles.get("hospital_load") or {}).items()
        },
        "factory": {
            int(tick): float(value) for tick, value in (profiles.get("factory_load") or {}).items()
        },
        "office": {
            int(tick): float(value) for tick, value in (profiles.get("office_load") or {}).items()
        },
        "house_load": canonical_house_series,
    }

    has_wind_range = _series_has_values(wind_from) and _series_has_values(wind_to)
    has_solar_east_west = _series_has_values(sun_east) and _series_has_values(sun_west)
    has_full_categories = all(
        _series_has_values(raw_category_series.get(key))
        for key in ("hospital", "factory", "house_a", "house_b")
    )
    has_canonical_generation = bool(
        (factors.get("wind_factor") or {}) and (factors.get("solar_factor") or {})
    )
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
        "solar_share_pct": (
            round((solar_generation_sum / generation_sum) * 100.0, 2)
            if generation_sum > 0
            else None
        ),
        "wind_share_pct": (
            round((wind_generation_sum / generation_sum) * 100.0, 2) if generation_sum > 0 else None
        ),
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


def _sum_series_dicts(*series_list: Dict[int, float]) -> Dict[int, float]:
    merged: Dict[int, float] = {}
    ticks = sorted({int(tick) for series in series_list for tick in (series or {}).keys()})
    for tick in ticks:
        values = [
            float((series or {}).get(int(tick), 0.0) or 0.0)
            for series in series_list
            if int(tick) in (series or {})
        ]
        if not values:
            continue
        merged[int(tick)] = float(sum(values))
    return merged


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


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _round_metric(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)
