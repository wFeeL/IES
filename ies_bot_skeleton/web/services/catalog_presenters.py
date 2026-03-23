from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..models import ObjectType
from .ui_text import category_label

FIELD_LABELS: Dict[str, str] = {
    "contract_rub_per_tick": "Тариф обслуживания за такт",
    "generation_mw": "Генерация, МВт",
    "expected_consumption_mw": "Ожидаемое потребление, МВт",
    "forecast_sensitivity": "Чувствительность к прогнозу",
    "profile": "Профиль",
    "tariff_rub_per_mw_tick": "Тариф подключения/обслуживания, руб/МВт·такт",
    "elasticity": "Эластичность спроса",
    "reference_tariff": "Опорный тариф",
    "capacity_mw_tick": "Ёмкость, МВт·тик",
    "charge_rate_mw": "Скорость заряда, МВт",
    "charge_rate_mw_tick": "Скорость заряда, МВт·такт",
    "discharge_rate_mw": "Скорость разряда, МВт",
    "discharge_rate_mw_tick": "Скорость разряда, МВт·такт",
    "efficiency": "КПД",
    "roundtrip_efficiency": "КПД цикла",
    "ports": "Порты",
    "soft_flow_limit_mw": "Лимит потока, МВт",
    "maintenance_cost_per_tick": "Сервисные расходы за такт",
    "district": "Район",
    "requires_substation": "Требуется подстанция",
    "depends_on_sun": "Зависит от солнца",
    "depends_on_wind": "Зависит от ветра",
    "wind_channel": "Канал ветра",
    "cut_in_mps": "Cut-in, м/с",
    "rated_mps": "Rated, м/с",
    "cut_out_mps": "Cut-out, м/с",
    "secondary_connection_point": "Второй ввод",
}

GLOSSARY_GROUPS: List[Dict[str, Any]] = [
    {
        "title": "Коды объектов",
        "items": [
            ("house_a", "дома типа A"),
            ("house_b", "дома типа B"),
            ("office", "офис"),
            ("factory", "завод"),
            ("hospital", "больница"),
            ("solar", "СЭС"),
            ("wind", "ВЭС"),
            ("storage", "накопитель"),
            ("main_substation", "главная подстанция"),
            ("mini_substation", "мини-подстанция"),
        ],
    },
    {
        "title": "Категории",
        "items": [
            ("consumer", "потребители"),
            ("generator", "генераторы"),
            ("storage", "накопители"),
            ("infrastructure", "инфраструктура"),
        ],
    },
    {
        "title": "Поля параметров",
        "items": [
            ("generation_mw", "объём генерации"),
            ("expected_consumption_mw", "ожидаемое потребление"),
            ("elasticity", "эластичность спроса"),
            ("contract_rub_per_tick", "тариф обслуживания за такт"),
            ("tariff_rub_per_mw_tick", "тариф потребителя"),
            ("wind_channel", "отдельный wind channel"),
            ("roundtrip_efficiency", "параметр накопителя"),
            ("ports", "число подключаемых линий"),
        ],
    },
    {
        "title": "Прогнозный контекст",
        "items": [
            ("forecast", "активный пользовательский прогноз"),
            ("bundled_forecast", "встроенный прогноз тестовой игры"),
        ],
    },
]

KEY_FIELDS_BY_CATEGORY: Dict[str, List[str]] = {
    "consumer": [
        "tariff_rub_per_mw_tick",
        "expected_consumption_mw",
        "elasticity",
        "forecast_sensitivity",
    ],
    "generator": [
        "contract_rub_per_tick",
        "generation_mw",
        "efficiency",
        "wind_channel",
    ],
    "storage": [
        "capacity_mw_tick",
        "charge_rate_mw",
        "discharge_rate_mw",
        "efficiency",
    ],
    "infrastructure": [
        "ports",
        "soft_flow_limit_mw",
        "wear_impact",
        "district",
    ],
}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _present_params(keys: Iterable[str], params: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for key in keys:
        if key not in params:
            continue
        rows.append(
            {
                "key": key,
                "label": FIELD_LABELS.get(key, key),
                "value": _format_value(params[key]),
            }
        )
    return rows


def build_catalog_sections(rows: Iterable[ObjectType]) -> List[Dict[str, Any]]:
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if bool((row.rules_json or {}).get("hidden_from_ui")):
            continue
        params = dict(row.default_parameters_json or {})
        key_fields = KEY_FIELDS_BY_CATEGORY.get(row.category, [])
        details_fields = [key for key in params.keys() if key not in key_fields]
        sections.setdefault(row.category, []).append(
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "category": row.category,
                "category_label": category_label(row.category),
                "subtype": row.subtype or "—",
                "description": row.description or "Описание не задано.",
                "status_label": "активен" if row.is_active else "отключен",
                "is_active": bool(row.is_active),
                "highlights": _present_params(key_fields, params),
                "details": _present_params(details_fields, params),
            }
        )

    out: List[Dict[str, Any]] = []
    for code, items in sections.items():
        out.append({"code": code, "label": category_label(code), "rows": items})
    return out


def glossary_groups() -> List[Dict[str, Any]]:
    return [{"title": row["title"], "rows": list(row["items"])} for row in GLOSSARY_GROUPS]
