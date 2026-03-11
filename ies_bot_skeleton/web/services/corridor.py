from __future__ import annotations

from typing import Any, Dict, Optional


DEFAULT_CORRIDOR_SETTINGS: Dict[str, Optional[float]] = {
    "consumer_load_pct": 10.0,
    "producer_generation_pct": 10.0,
    "solar_output_pct": None,
    "wind_output_pct": None,
}

_NUMERIC_FIELDS = ("consumer_load_pct", "producer_generation_pct")
_OPTIONAL_FIELDS = ("solar_output_pct", "wind_output_pct")


def _to_float(value: Any, *, field_name: str, allow_none: bool = False) -> Optional[float]:
    if value in (None, "", "null"):
        if allow_none:
            return None
        raise ValueError(f"{field_name} обязателен")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} должен быть числом") from exc
    if out < 0.0 or out > 100.0:
        raise ValueError(f"{field_name} должен быть в диапазоне 0..100")
    return round(out, 2)


def ruleset_default_corridor_settings(config: Dict[str, Any] | None) -> Dict[str, Optional[float]]:
    cfg = dict(config or {})
    base = ((cfg.get("scenarios") or {}).get("corridor") or {}) if cfg else {}
    wind_pct = round(float(base.get("wind_mul", 0.10) or 0.10) * 100.0, 2)
    solar_pct = round(float(base.get("solar_mul", 0.10) or 0.10) * 100.0, 2)
    load_pct = round(float(base.get("load_mul", 0.10) or 0.10) * 100.0, 2)
    producer_pct = wind_pct if wind_pct == solar_pct else max(wind_pct, solar_pct)
    return {
        "consumer_load_pct": load_pct,
        "producer_generation_pct": producer_pct,
        "solar_output_pct": None if solar_pct == producer_pct else solar_pct,
        "wind_output_pct": None if wind_pct == producer_pct else wind_pct,
    }


def normalize_corridor_settings(
    payload: Dict[str, Any] | None,
    *,
    fallback: Dict[str, Any] | None = None,
) -> Dict[str, Optional[float]]:
    base: Dict[str, Any] = dict(DEFAULT_CORRIDOR_SETTINGS)
    if fallback:
        for key in (*_NUMERIC_FIELDS, *_OPTIONAL_FIELDS):
            if key in fallback:
                base[key] = fallback[key]
    incoming = dict(payload or {})
    for key in (*_NUMERIC_FIELDS, *_OPTIONAL_FIELDS):
        if key in incoming:
            base[key] = incoming[key]

    normalized: Dict[str, Optional[float]] = {}
    for key in _NUMERIC_FIELDS:
        normalized[key] = _to_float(base.get(key), field_name=key, allow_none=False)
    for key in _OPTIONAL_FIELDS:
        normalized[key] = _to_float(base.get(key), field_name=key, allow_none=True)
    return normalized


def corridor_settings_to_assumptions(
    settings: Dict[str, Any] | None,
) -> Dict[str, float]:
    normalized = normalize_corridor_settings(settings)
    producer_pct = float(normalized["producer_generation_pct"] or 0.0)
    solar_pct = (
        float(normalized["solar_output_pct"])
        if normalized["solar_output_pct"] is not None
        else producer_pct
    )
    wind_pct = (
        float(normalized["wind_output_pct"])
        if normalized["wind_output_pct"] is not None
        else producer_pct
    )
    return {
        "load_mul": round(float(normalized["consumer_load_pct"] or 0.0) / 100.0, 4),
        "solar_mul": round(solar_pct / 100.0, 4),
        "wind_mul": round(wind_pct / 100.0, 4),
    }


def build_corridor_summary(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized = normalize_corridor_settings(settings)
    assumptions = corridor_settings_to_assumptions(normalized)
    producer_pct = float(normalized["producer_generation_pct"] or 0.0)
    solar_pct = (
        float(normalized["solar_output_pct"])
        if normalized["solar_output_pct"] is not None
        else producer_pct
    )
    wind_pct = (
        float(normalized["wind_output_pct"])
        if normalized["wind_output_pct"] is not None
        else producer_pct
    )
    return {
        "settings": normalized,
        "assumptions": assumptions,
        "text": (
            f"Потребители +/- {normalized['consumer_load_pct']:.1f}%, "
            f"генерация +/- {producer_pct:.1f}%."
        ),
        "consumer_text": f"Нагрузка потребителей +/- {normalized['consumer_load_pct']:.1f}%",
        "producer_text": f"Общая генерация +/- {producer_pct:.1f}%",
        "solar_text": f"Солнечная генерация +/- {solar_pct:.1f}%",
        "wind_text": f"Ветровая генерация +/- {wind_pct:.1f}%",
        "worst_case": (
            f"Худший сценарий: нагрузка +{normalized['consumer_load_pct']:.1f}%, "
            f"ветер -{wind_pct:.1f}%, солнце -{solar_pct:.1f}%."
        ),
        "best_case": (
            f"Лучший сценарий: нагрузка -{normalized['consumer_load_pct']:.1f}%, "
            f"ветер +{wind_pct:.1f}%, солнце +{solar_pct:.1f}%."
        ),
    }

