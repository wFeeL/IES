from __future__ import annotations

from typing import Any, Dict, List

from ..models import ObjectType

PARAMETER_LABELS: Dict[str, str] = {
    "tariff_rub_per_mw_tick": "Тариф, руб/МВт·тик",
    "expected_consumption_mw": "Ожидаемое потребление, МВт",
    "profile": "Профиль",
    "eco_score": "Эко-оценка",
    "maintenance_cost": "Стоимость обслуживания",
    "tax": "Налог",
    "forecast_sensitivity": "Чувствительность к прогнозу",
    "contract_rub_per_tick": "Контрактная цена",
    "generation_mw": "Генерация, МВт",
    "fuel_price": "Цена топлива",
    "eco_tax_fuel": "Экологический налог",
    "capacity_mw_tick": "Ёмкость, МВт·тик",
    "charge_rate_mw": "Скорость заряда, МВт",
    "discharge_rate_mw": "Скорость разряда, МВт",
    "efficiency": "КПД",
    "ports": "Порты",
    "soft_flow_limit_mw": "Лимит потока, МВт",
    "district": "Район",
    "wear_impact": "Влияние на износ",
    "depends_on_sun": "Зависит от солнца",
    "depends_on_wind": "Зависит от ветра",
    "requires_substation": "Требуется подстанция",
    "is_root": "Корневой узел",
}


def _field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def parameter_rows(
    object_type: ObjectType | None,
    *,
    current_parameters: Dict[str, Any] | None = None,
    submitted_values: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    if object_type is None:
        return []

    defaults = dict(object_type.default_parameters_json or {})
    current = dict(current_parameters or {})
    keys: List[str] = []
    for key in list(object_type.editable_fields_json or []) + list(defaults.keys()) + list(current.keys()):
        if key not in keys:
            keys.append(key)

    rows: List[Dict[str, Any]] = []
    for key in keys:
        default_value = current.get(key, defaults.get(key))
        field_type = _field_type(default_value)
        if submitted_values is not None and key in submitted_values:
            value = submitted_values[key]
        else:
            value = default_value
        rows.append(
            {
                "key": key,
                "label": PARAMETER_LABELS.get(key, key),
                "type": field_type,
                "value": value,
                "default_value": defaults.get(key),
                "is_editable": key in set(object_type.editable_fields_json or []) or key in current,
            }
        )
    return rows


def parameters_from_form(
    object_type: ObjectType | None,
    form_values: Dict[str, Any],
    *,
    current_parameters: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if object_type is None:
        return {}

    values: Dict[str, Any] = {}
    rows = parameter_rows(
        object_type,
        current_parameters=current_parameters,
    )
    for row in rows:
        key = row["key"]
        raw_value = form_values.get(key)
        if row["type"] == "boolean":
            values[key] = raw_value in {"1", "true", "on", "yes"}
            continue
        if raw_value in (None, ""):
            continue
        if row["type"] == "number":
            try:
                values[key] = float(raw_value)
            except ValueError as exc:
                raise ValueError(f"Параметр «{row['label']}» должен быть числом") from exc
            continue
        values[key] = str(raw_value)
    return values
