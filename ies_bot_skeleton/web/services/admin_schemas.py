from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from flask import Request

from .ruleset import DEFAULT_STRATEGY_PROFILES, DEFAULT_WEIGHTS, build_default_ruleset_config

OBJECT_TYPE_CATEGORY_CHOICES = [
    ("consumer", "Потребители"),
    ("generator", "Генераторы"),
    ("storage", "Накопители"),
    ("infrastructure", "Инфраструктура"),
]

OBJECT_TYPE_SUBTYPE_CHOICES = [
    ("", "—"),
    ("residential", "Жилой"),
    ("office", "Офис"),
    ("industry", "Промышленный"),
    ("solar", "Солнечная генерация"),
    ("wind", "Ветровая генерация"),
    ("thermal", "Тепловая генерация"),
    ("battery", "Батарея"),
    ("main", "Главная подстанция"),
    ("miniA", "Мини-подстанция A"),
    ("miniB", "Мини-подстанция B"),
]

KNOWN_OBJECT_FIELDS = {
    "tariff_rub_per_mw_tick",
    "expected_consumption_mw",
    "profile",
    "eco_score",
    "maintenance_cost",
    "tax",
    "forecast_sensitivity",
    "contract_rub_per_tick",
    "generation_mw",
    "fuel_price",
    "eco_tax_fuel",
    "capacity_mw_tick",
    "charge_rate_mw",
    "discharge_rate_mw",
    "efficiency",
    "ports",
    "soft_flow_limit_mw",
    "district",
    "wear_impact",
    "depends_on_sun",
    "depends_on_wind",
}

KNOWN_RULE_FIELDS = {"requires_substation", "forbid_mixed_gen_load", "is_root"}

OBJECT_TYPE_SECTIONS = [
    {
        "key": "consumer",
        "title": "Основные параметры",
        "categories": {"consumer"},
        "fields": [
            ("tariff_rub_per_mw_tick", "number", "Тариф, руб/МВт·тик"),
            ("expected_consumption_mw", "number", "Ожидаемое потребление, МВт"),
            ("profile", "text", "Профиль"),
            ("eco_score", "number", "Эко-оценка"),
            ("maintenance_cost", "number", "Стоимость обслуживания"),
            ("tax", "number", "Налог"),
            ("forecast_sensitivity", "number", "Чувствительность к прогнозу"),
        ],
    },
    {
        "key": "generator_common",
        "title": "Основные параметры",
        "categories": {"generator"},
        "fields": [
            ("contract_rub_per_tick", "number", "Контрактная цена"),
            ("generation_mw", "number", "Генерация, МВт"),
            ("efficiency", "number", "КПД"),
            ("eco_score", "number", "Эко-оценка"),
            ("forecast_sensitivity", "number", "Чувствительность к прогнозу"),
        ],
    },
    {
        "key": "generator_solar",
        "title": "Солнечная генерация",
        "categories": {"generator"},
        "subtypes": {"solar"},
        "fields": [("depends_on_sun", "boolean", "Зависит от солнца")],
    },
    {
        "key": "generator_wind",
        "title": "Ветровая генерация",
        "categories": {"generator"},
        "subtypes": {"wind"},
        "fields": [("depends_on_wind", "boolean", "Зависит от ветра")],
    },
    {
        "key": "generator_thermal",
        "title": "Тепловая генерация",
        "categories": {"generator"},
        "subtypes": {"thermal"},
        "fields": [
            ("fuel_price", "number", "Цена топлива"),
            ("eco_tax_fuel", "number", "Экологический налог"),
        ],
    },
    {
        "key": "storage",
        "title": "Основные параметры",
        "categories": {"storage"},
        "fields": [
            ("contract_rub_per_tick", "number", "Контрактная цена"),
            ("capacity_mw_tick", "number", "Ёмкость, МВт·тик"),
            ("charge_rate_mw", "number", "Скорость заряда, МВт"),
            ("discharge_rate_mw", "number", "Скорость разряда, МВт"),
            ("efficiency", "number", "КПД"),
            ("eco_score", "number", "Эко-оценка"),
            ("forecast_sensitivity", "number", "Чувствительность к прогнозу"),
        ],
    },
    {
        "key": "infrastructure",
        "title": "Основные параметры",
        "categories": {"infrastructure"},
        "fields": [
            ("ports", "number", "Порты"),
            ("contract_rub_per_tick", "number", "Контрактная цена"),
            ("soft_flow_limit_mw", "number", "Лимит потока, МВт"),
            ("district", "text", "Район"),
            ("wear_impact", "number", "Влияние на износ"),
        ],
    },
]

OBJECT_TYPE_RULE_FIELDS = [
    ("requires_substation", "Требуется подстанция"),
    ("forbid_mixed_gen_load", "Запрет смешанной генерации и нагрузки"),
    ("is_root", "Корневой узел"),
]

RULESET_SECTION_FIELDS = [
    {
        "key": "scenarios",
        "title": "Сценарии",
        "help": "Базовые, худшие и лучшие множители для ветра, солнца и нагрузки.",
        "fields": [
            ("scenario_base_wind", "number", "Base: ветер"),
            ("scenario_base_solar", "number", "Base: солнце"),
            ("scenario_base_load", "number", "Base: нагрузка"),
            ("scenario_worst_wind", "number", "Worst: ветер"),
            ("scenario_worst_solar", "number", "Worst: солнце"),
            ("scenario_worst_load", "number", "Worst: нагрузка"),
            ("scenario_best_wind", "number", "Best: ветер"),
            ("scenario_best_solar", "number", "Best: солнце"),
            ("scenario_best_load", "number", "Best: нагрузка"),
        ],
    },
    {
        "key": "market",
        "title": "Рынок",
        "fields": [
            ("market_external_buy_price", "number", "Покупка извне"),
            ("market_external_sell_price", "number", "Продажа извне"),
            ("market_instant_buy_price", "number", "Мгновенная покупка"),
            ("market_instant_sell_price", "number", "Мгновенная продажа"),
            ("market_market_max_power", "number", "Лимит мощности рынка"),
            ("market_instant_buy_max_power", "number", "Лимит мгновенной покупки"),
            ("market_instant_sell_max_power", "number", "Лимит мгновенной продажи"),
        ],
    },
    {
        "key": "network",
        "title": "Сеть",
        "fields": [
            ("network_mode", "text", "Режим сети"),
            ("network_soft_flow_mw", "number", "Мягкий лимит потока"),
            ("network_main_substation_limit_mw", "number", "Лимит главной подстанции"),
            ("network_line_max_power_mw", "number", "Лимит линии"),
            ("network_default_connection_point", "text", "Точка подключения по умолчанию"),
            ("network_loss_alpha", "number", "Коэффициент потерь"),
            ("network_wear_overload_mw", "number", "Порог перегруза"),
            ("network_wear_risk_penalty_rub", "number", "Штраф за риск износа"),
            ("network_loss_tax", "number", "Налог на потери"),
        ],
    },
    {
        "key": "connection_losses",
        "title": "Подключения",
        "fields": [
            ("connection_loss_A", "number", "Потери A, %"),
            ("connection_loss_B", "number", "Потери B, %"),
            ("connection_loss_C", "number", "Потери C, %"),
            ("connection_loss_D", "number", "Потери D, %"),
            ("connection_loss_E", "number", "Потери E, %"),
            ("connection_loss_F", "number", "Потери F, %"),
            ("connection_loss_G", "number", "Потери G, %"),
        ],
    },
    {
        "key": "eco",
        "title": "Экология",
        "fields": [
            ("eco_wind_points_per_mw_tick", "number", "Очки ветра"),
            ("eco_solar_points_per_mw_tick", "number", "Очки солнца"),
            ("eco_storage_discharge_points_per_mw_tick", "number", "Очки разряда накопителя"),
            ("eco_eco_point_value_rub", "number", "Стоимость эко-очка"),
        ],
    },
    {
        "key": "storage",
        "title": "Накопители",
        "fields": [
            ("storage_capacity_mw_tick", "number", "Ёмкость"),
            ("storage_charge_rate_mw", "number", "Скорость заряда"),
            ("storage_discharge_rate_mw", "number", "Скорость разряда"),
            ("storage_leak_fraction_per_tick", "number", "Потери за тик"),
        ],
    },
    {
        "key": "tps",
        "title": "ТЭС",
        "fields": [
            ("tps_fuel_max", "number", "Максимум топлива"),
            ("tps_eta_nominal", "number", "Номинальный КПД"),
            ("tps_fuel_price", "number", "Цена топлива"),
            ("tps_eco_tax_fuel", "number", "Эко-налог топлива"),
        ],
    },
    {
        "key": "auction",
        "title": "Аукцион",
        "fields": [
            ("auction_allpay_limit", "number", "Лимит all-pay"),
            ("auction_starting_budget", "number", "Стартовый бюджет"),
            ("auction_tie_break_threshold", "number", "Порог tie-break"),
            ("auction_pwin_default", "number", "Вероятность победы по умолчанию"),
            ("auction_pwin_min", "number", "Минимальная вероятность победы"),
            ("auction_pwin_max", "number", "Максимальная вероятность победы"),
            ("auction_serious_competitors_default", "number", "Серьёзные конкуренты (база)"),
            ("auction_serious_competitors_min", "number", "Серьёзные конкуренты (мин)"),
            ("auction_serious_competitors_max", "number", "Серьёзные конкуренты (макс)"),
            ("auction_rank_weight", "number", "Вес ранга"),
            ("auction_synergy_weight", "number", "Вес синергии"),
            ("auction_scarcity_weight", "number", "Вес редкости"),
            ("auction_scope_weight", "number", "Вес масштаба лота"),
            ("auction_safe_multiplier", "number", "Множитель safe bid"),
            ("auction_balanced_multiplier", "number", "Множитель balanced bid"),
            ("auction_aggressive_multiplier", "number", "Множитель aggressive bid"),
            ("auction_volatility_lambda_bid", "number", "Штраф волатильности в bid-модели"),
            ("auction_bid_model_version", "text", "Версия bid-модели"),
            ("auction_conservative_utility_method", "text", "Метод conservative utility"),
        ],
    },
    {
        "key": "evaluation",
        "title": "Оценка",
        "fields": [
            ("evaluation_weight_base", "number", "Вес base"),
            ("evaluation_weight_worst", "number", "Вес worst"),
            ("evaluation_weight_best", "number", "Вес best"),
            ("evaluation_weight_custom", "number", "Вес custom"),
            ("evaluation_risk_lambda", "number", "Коэффициент риска"),
            ("evaluation_volatility_lambda", "number", "Коэффициент волатильности"),
            ("evaluation_reserve_margin_abs", "number", "Мин. резерв ставки"),
            ("evaluation_reserve_margin_share", "number", "Доля резерва ставки"),
            (
                "evaluation_storage_operating_cost_per_mwh_throughput",
                "number",
                "Опер. расход накопителя за MWh",
            ),
        ],
    },
    {
        "key": "weights",
        "title": "Веса оценки лота",
        "fields": [
            ("w1_economy", "number", "w1 Экономика"),
            ("w2_balance", "number", "w2 Баланс"),
            ("w3_stability", "number", "w3 Стабильность"),
            ("w4_green", "number", "w4 Экология"),
            ("w5_flex", "number", "w5 Гибкость"),
            ("w6_risk", "number", "w6 Риск"),
            ("w7_wear", "number", "w7 Износ"),
            ("w8_violations", "number", "w8 Нарушения"),
        ],
    },
]


def object_type_section_definitions() -> List[Dict[str, Any]]:
    return [deepcopy(section) for section in OBJECT_TYPE_SECTIONS]


def object_type_rule_definitions() -> List[tuple[str, str]]:
    return list(OBJECT_TYPE_RULE_FIELDS)


def object_type_editable_choices(category: str, subtype: str) -> List[tuple[str, str]]:
    choices: List[tuple[str, str]] = []
    for section in OBJECT_TYPE_SECTIONS:
        if category not in section["categories"]:
            continue
        if section.get("subtypes") and subtype not in section["subtypes"]:
            continue
        for key, _, label in section["fields"]:
            choices.append((key, label))
    return choices


def _truthy(request: Request, key: str) -> bool:
    return request.form.get(key) in {"1", "true", "on", "yes"}


def _to_number(request: Request, key: str) -> float | None:
    raw = (request.form.get(key) or "").strip()
    if not raw:
        return None
    return float(raw)


def _to_text(request: Request, key: str) -> str | None:
    raw = (request.form.get(key) or "").strip()
    return raw or None


def object_type_payload_from_request(
    request: Request,
    *,
    category: str,
    subtype: str,
    base_defaults: Dict[str, Any] | None = None,
    base_rules: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    defaults = {k: v for k, v in dict(base_defaults or {}).items() if k not in KNOWN_OBJECT_FIELDS}
    rules = {k: v for k, v in dict(base_rules or {}).items() if k not in KNOWN_RULE_FIELDS}

    editable_fields: List[str] = []
    for section in OBJECT_TYPE_SECTIONS:
        if category not in section["categories"]:
            continue
        if section.get("subtypes") and subtype not in section["subtypes"]:
            continue
        for field_key, field_type, _ in section["fields"]:
            if field_type == "boolean":
                defaults[field_key] = _truthy(request, field_key)
            elif field_type == "number":
                value = _to_number(request, field_key)
                if value is not None:
                    defaults[field_key] = value
            else:
                value = _to_text(request, field_key)
                if value is not None:
                    defaults[field_key] = value
            if _truthy(request, f"editable_{field_key}"):
                editable_fields.append(field_key)

    for field_key, _ in OBJECT_TYPE_RULE_FIELDS:
        rules[field_key] = _truthy(request, field_key)

    return {
        "default_parameters": defaults,
        "editable_fields": editable_fields,
        "rules": rules,
    }


def object_type_field_value(
    defaults: Dict[str, Any],
    rules: Dict[str, Any],
    field_key: str,
    field_type: str,
) -> Any:
    source = rules if field_key in KNOWN_RULE_FIELDS else defaults
    value = source.get(field_key)
    if field_type == "boolean":
        return bool(value)
    return value


def ruleset_sections() -> List[Dict[str, Any]]:
    return [deepcopy(section) for section in RULESET_SECTION_FIELDS]


def _get_nested(data: Dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def ruleset_form_values(
    config_json: Dict[str, Any] | None, model_settings_json: Dict[str, Any] | None
) -> Dict[str, Any]:
    base_config = build_default_ruleset_config()
    config = deepcopy(base_config)
    config.update(dict(config_json or {}))
    model_settings = deepcopy(dict(model_settings_json or {}))

    weighted_expected = dict(
        _get_nested(config, "evaluation", "weighted_expected", default={}) or {}
    )
    weighted_expected.update(
        dict(_get_nested(model_settings, "evaluation", "weighted_expected", default={}) or {})
    )
    lot_weights = dict(config.get("lot_score_weights", {}) or {})
    lot_weights.update(dict(model_settings.get("lot_score_weights", {}) or {}))
    strategy_profiles = deepcopy(dict(config.get("strategy_profiles", {}) or {}))
    for code, profile in dict(model_settings.get("strategy_profiles", {}) or {}).items():
        merged = dict(strategy_profiles.get(code, {}) or {})
        merged.update(dict(profile or {}))
        strategy_profiles[code] = merged

    connection_loss = dict(
        _get_nested(config, "network", "connection_loss_pct_by_point", default={}) or {}
    )
    values = {
        "scenario_base_wind": _get_nested(config, "scenarios", "base", "wind", default=1.0),
        "scenario_base_solar": _get_nested(config, "scenarios", "base", "solar", default=1.0),
        "scenario_base_load": _get_nested(config, "scenarios", "base", "load", default=1.0),
        "scenario_worst_wind": _get_nested(config, "scenarios", "worst", "wind", default=1.0),
        "scenario_worst_solar": _get_nested(config, "scenarios", "worst", "solar", default=0.5),
        "scenario_worst_load": _get_nested(config, "scenarios", "worst", "load", default=1.5),
        "scenario_best_wind": _get_nested(config, "scenarios", "best", "wind", default=1.0),
        "scenario_best_solar": _get_nested(config, "scenarios", "best", "solar", default=1.5),
        "scenario_best_load": _get_nested(config, "scenarios", "best", "load", default=0.5),
        "market_external_buy_price": _get_nested(
            config, "market", "external_buy_price", default=10.0
        ),
        "market_external_sell_price": _get_nested(
            config, "market", "external_sell_price", default=2.0
        ),
        "market_instant_buy_price": _get_nested(
            config, "market", "instant_buy_price", default=10.0
        ),
        "market_instant_sell_price": _get_nested(
            config, "market", "instant_sell_price", default=2.0
        ),
        "market_market_max_power": _get_nested(
            config, "market", "market_max_power", default=1000000.0
        ),
        "market_instant_buy_max_power": _get_nested(
            config, "market", "instant_buy_max_power", default=0.0
        ),
        "market_instant_sell_max_power": _get_nested(
            config, "market", "instant_sell_max_power", default=0.0
        ),
        "network_mode": _get_nested(config, "network", "mode", default="branches"),
        "network_soft_flow_mw": _get_nested(config, "network", "soft_flow_mw", default=40.0),
        "network_main_substation_limit_mw": _get_nested(
            config, "network", "main_substation_limit_mw", default=40.0
        ),
        "network_line_max_power_mw": _get_nested(
            config, "network", "line_max_power_mw", default=15.0
        ),
        "network_default_connection_point": _get_nested(
            config, "network", "default_connection_point", default="A"
        ),
        "network_loss_alpha": _get_nested(config, "network", "loss_alpha", default=0.0),
        "network_wear_overload_mw": _get_nested(
            config, "network", "wear_overload_mw", default=9999.0
        ),
        "network_wear_risk_penalty_rub": _get_nested(
            config, "network", "wear_risk_penalty_rub", default=0.0
        ),
        "network_loss_tax": _get_nested(config, "network", "loss_tax", default=1.0),
        "eco_wind_points_per_mw_tick": _get_nested(
            config, "eco", "wind_points_per_mw_tick", default=1.0
        ),
        "eco_solar_points_per_mw_tick": _get_nested(
            config, "eco", "solar_points_per_mw_tick", default=1.0
        ),
        "eco_storage_discharge_points_per_mw_tick": _get_nested(
            config, "eco", "storage_discharge_points_per_mw_tick", default=0.0
        ),
        "eco_eco_point_value_rub": _get_nested(config, "eco", "eco_point_value_rub", default=2.0),
        "storage_capacity_mw_tick": _get_nested(
            config, "storage", "capacity_mw_tick", default=20.0
        ),
        "storage_charge_rate_mw": _get_nested(config, "storage", "charge_rate_mw", default=5.0),
        "storage_discharge_rate_mw": _get_nested(
            config, "storage", "discharge_rate_mw", default=5.0
        ),
        "storage_leak_fraction_per_tick": _get_nested(
            config, "storage", "leak_fraction_per_tick", default=0.05
        ),
        "tps_fuel_max": _get_nested(config, "tps", "fuel_max", default=15.0),
        "tps_eta_nominal": _get_nested(config, "tps", "eta_nominal", default=1.0),
        "tps_fuel_price": _get_nested(config, "tps", "fuel_price", default=0.5),
        "tps_eco_tax_fuel": _get_nested(config, "tps", "eco_tax_fuel", default=0.0),
        "auction_allpay_limit": _get_nested(config, "auction", "allpay_limit", default=200.0),
        "auction_starting_budget": _get_nested(config, "auction", "starting_budget", default=200.0),
        "auction_tie_break_threshold": _get_nested(
            config, "auction", "tie_break_threshold", default=2.0
        ),
        "auction_pwin_default": _get_nested(config, "auction", "pwin_default", default=0.35),
        "auction_pwin_min": _get_nested(config, "auction", "pwin_min", default=0.08),
        "auction_pwin_max": _get_nested(config, "auction", "pwin_max", default=0.88),
        "auction_serious_competitors_default": _get_nested(
            config, "auction", "serious_competitors_default", default=3.0
        ),
        "auction_serious_competitors_min": _get_nested(
            config, "auction", "serious_competitors_min", default=2.0
        ),
        "auction_serious_competitors_max": _get_nested(
            config, "auction", "serious_competitors_max", default=7.0
        ),
        "auction_rank_weight": _get_nested(config, "auction", "rank_weight", default=0.22),
        "auction_synergy_weight": _get_nested(config, "auction", "synergy_weight", default=0.12),
        "auction_scarcity_weight": _get_nested(config, "auction", "scarcity_weight", default=0.08),
        "auction_scope_weight": _get_nested(config, "auction", "scope_weight", default=0.06),
        "auction_safe_multiplier": _get_nested(config, "auction", "safe_multiplier", default=0.82),
        "auction_balanced_multiplier": _get_nested(
            config, "auction", "balanced_multiplier", default=1.0
        ),
        "auction_aggressive_multiplier": _get_nested(
            config, "auction", "aggressive_multiplier", default=1.22
        ),
        "auction_volatility_lambda_bid": _get_nested(
            config, "auction", "volatility_lambda_bid", default=0.15
        ),
        "auction_bid_model_version": _get_nested(
            config, "auction", "auction_bid_model_version", default="strategic_anchor_v4"
        ),
        "auction_conservative_utility_method": _get_nested(
            config,
            "auction",
            "conservative_utility_method",
            default="weighted_expected_minus_volatility",
        ),
        "evaluation_weight_base": weighted_expected.get("base", 0.50),
        "evaluation_weight_worst": weighted_expected.get("worst", 0.35),
        "evaluation_weight_best": weighted_expected.get("best", 0.15),
        "evaluation_weight_custom": weighted_expected.get("custom", 0.0),
        "evaluation_risk_lambda": _get_nested(
            model_settings,
            "evaluation",
            "risk_lambda",
            default=_get_nested(config, "evaluation", "risk_lambda", default=0.25),
        ),
        "evaluation_volatility_lambda": _get_nested(
            model_settings,
            "evaluation",
            "volatility_lambda",
            default=_get_nested(config, "evaluation", "volatility_lambda", default=0.15),
        ),
        "evaluation_reserve_margin_abs": _get_nested(
            model_settings,
            "evaluation",
            "reserve_margin_abs",
            default=_get_nested(config, "evaluation", "reserve_margin_abs", default=5.0),
        ),
        "evaluation_reserve_margin_share": _get_nested(
            model_settings,
            "evaluation",
            "reserve_margin_share",
            default=_get_nested(config, "evaluation", "reserve_margin_share", default=0.10),
        ),
        "evaluation_storage_operating_cost_per_mwh_throughput": _get_nested(
            model_settings,
            "evaluation",
            "storage_operating_cost_per_mwh_throughput",
            default=_get_nested(
                config,
                "evaluation",
                "storage_operating_cost_per_mwh_throughput",
                default=0.0,
            ),
        ),
    }
    for point in "ABCDEFG":
        values[f"connection_loss_{point}"] = connection_loss.get(point, 0.0)
    for weight_key, default_value in DEFAULT_WEIGHTS.items():
        values[weight_key] = lot_weights.get(weight_key, default_value)
    for strategy_code, defaults in DEFAULT_STRATEGY_PROFILES.items():
        merged = dict(defaults)
        merged.update(dict(strategy_profiles.get(strategy_code, {}) or {}))
        for weight_key in DEFAULT_WEIGHTS:
            values[f"profile__{strategy_code}__{weight_key}"] = merged.get(weight_key, "")
    return values


def ruleset_payload_from_request(
    request: Request,
    *,
    base_config: Dict[str, Any] | None = None,
    base_model_settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    config = deepcopy(dict(base_config or {}))
    model_settings = deepcopy(dict(base_model_settings or {}))

    def num(name: str, default: float = 0.0) -> float:
        raw = (request.form.get(name) or "").strip()
        return default if not raw else float(raw)

    def text(name: str, default: str = "") -> str:
        raw = (request.form.get(name) or "").strip()
        return raw or default

    config["scenarios"] = {
        "base": {
            "wind": num("scenario_base_wind", 1.0),
            "solar": num("scenario_base_solar", 1.0),
            "load": num("scenario_base_load", 1.0),
        },
        "worst": {
            "wind": num("scenario_worst_wind", 1.0),
            "solar": num("scenario_worst_solar", 0.5),
            "load": num("scenario_worst_load", 1.5),
        },
        "best": {
            "wind": num("scenario_best_wind", 1.0),
            "solar": num("scenario_best_solar", 1.5),
            "load": num("scenario_best_load", 0.5),
        },
    }
    market = deepcopy(dict(config.get("market", {}) or {}))
    market.update(
        {
            "external_buy_price": num("market_external_buy_price", 10.0),
            "external_sell_price": num("market_external_sell_price", 2.0),
            "instant_buy_price": num("market_instant_buy_price", 10.0),
            "instant_sell_price": num("market_instant_sell_price", 2.0),
            "market_max_power": num("market_market_max_power", 1000000.0),
            "instant_buy_max_power": num("market_instant_buy_max_power", 0.0),
            "instant_sell_max_power": num("market_instant_sell_max_power", 0.0),
        }
    )
    config["market"] = market

    network = deepcopy(dict(config.get("network", {}) or {}))
    network.update(
        {
            "mode": text("network_mode", "branches"),
            "soft_flow_mw": num("network_soft_flow_mw", 40.0),
            "main_substation_limit_mw": num("network_main_substation_limit_mw", 40.0),
            "line_max_power_mw": num("network_line_max_power_mw", 15.0),
            "default_connection_point": text("network_default_connection_point", "A"),
            "loss_alpha": num("network_loss_alpha", 0.0),
            "wear_overload_mw": num("network_wear_overload_mw", 9999.0),
            "wear_risk_penalty_rub": num("network_wear_risk_penalty_rub", 0.0),
            "loss_tax": num("network_loss_tax", 1.0),
            "connection_loss_pct_by_point": {
                point: num(f"connection_loss_{point}", 0.0) for point in "ABCDEFG"
            },
        }
    )
    config["network"] = network
    config["eco"] = {
        "wind_points_per_mw_tick": num("eco_wind_points_per_mw_tick", 1.0),
        "solar_points_per_mw_tick": num("eco_solar_points_per_mw_tick", 1.0),
        "storage_discharge_points_per_mw_tick": num(
            "eco_storage_discharge_points_per_mw_tick", 0.0
        ),
        "eco_point_value_rub": num("eco_eco_point_value_rub", 2.0),
    }
    config["storage"] = {
        "capacity_mw_tick": num("storage_capacity_mw_tick", 20.0),
        "charge_rate_mw": num("storage_charge_rate_mw", 5.0),
        "discharge_rate_mw": num("storage_discharge_rate_mw", 5.0),
        "leak_fraction_per_tick": num("storage_leak_fraction_per_tick", 0.05),
    }
    config["tps"] = {
        "fuel_max": num("tps_fuel_max", 15.0),
        "eta_nominal": num("tps_eta_nominal", 1.0),
        "fuel_price": num("tps_fuel_price", 0.5),
        "eco_tax_fuel": num("tps_eco_tax_fuel", 0.0),
    }
    auction = deepcopy(dict(config.get("auction", {}) or {}))
    auction.update(
        {
            "allpay_limit": num("auction_allpay_limit", 200.0),
            "starting_budget": num("auction_starting_budget", 200.0),
            "tie_break_threshold": num("auction_tie_break_threshold", 2.0),
            "pwin_default": num("auction_pwin_default", 0.35),
            "pwin_min": num("auction_pwin_min", 0.08),
            "pwin_max": num("auction_pwin_max", 0.88),
            "serious_competitors_default": num("auction_serious_competitors_default", 3.0),
            "serious_competitors_min": num("auction_serious_competitors_min", 2.0),
            "serious_competitors_max": num("auction_serious_competitors_max", 7.0),
            "rank_weight": num("auction_rank_weight", 0.22),
            "synergy_weight": num("auction_synergy_weight", 0.12),
            "scarcity_weight": num("auction_scarcity_weight", 0.08),
            "scope_weight": num("auction_scope_weight", 0.06),
            "safe_multiplier": num("auction_safe_multiplier", 0.82),
            "balanced_multiplier": num("auction_balanced_multiplier", 1.0),
            "aggressive_multiplier": num("auction_aggressive_multiplier", 1.22),
            "volatility_lambda_bid": num("auction_volatility_lambda_bid", 0.15),
            "auction_bid_model_version": text("auction_bid_model_version", "strategic_anchor_v4"),
            "conservative_utility_method": text(
                "auction_conservative_utility_method", "weighted_expected_minus_volatility"
            ),
        }
    )
    config["auction"] = auction

    weights = deepcopy(
        dict(
            model_settings.get("lot_score_weights", {})
            or dict(config.get("lot_score_weights", {}) or {})
        )
    )
    for weight_key, default_value in DEFAULT_WEIGHTS.items():
        weights[weight_key] = num(weight_key, default_value)
    config["lot_score_weights"] = deepcopy(weights)
    model_settings["lot_score_weights"] = weights

    profiles = deepcopy(
        dict(
            model_settings.get("strategy_profiles", {})
            or dict(config.get("strategy_profiles", {}) or {})
        )
    )
    for strategy_code, defaults in DEFAULT_STRATEGY_PROFILES.items():
        merged = dict(defaults)
        merged.update(dict(profiles.get(strategy_code, {}) or {}))
        for weight_key in DEFAULT_WEIGHTS:
            raw = (request.form.get(f"profile__{strategy_code}__{weight_key}") or "").strip()
            if raw:
                merged[weight_key] = float(raw)
            elif weight_key in merged and weight_key not in defaults:
                merged.pop(weight_key, None)
        profiles[strategy_code] = merged
    config["strategy_profiles"] = deepcopy(profiles)
    model_settings["strategy_profiles"] = profiles

    evaluation = deepcopy(
        dict(model_settings.get("evaluation", {}) or dict(config.get("evaluation", {}) or {}))
    )
    weighted_expected = deepcopy(dict(evaluation.get("weighted_expected", {}) or {}))
    weighted_expected.update(
        {
            "base": num("evaluation_weight_base", 0.50),
            "worst": num("evaluation_weight_worst", 0.35),
            "best": num("evaluation_weight_best", 0.15),
            "custom": num("evaluation_weight_custom", 0.0),
        }
    )
    evaluation["weighted_expected"] = weighted_expected
    evaluation["risk_lambda"] = num("evaluation_risk_lambda", 0.25)
    evaluation["volatility_lambda"] = num("evaluation_volatility_lambda", 0.15)
    evaluation["reserve_margin_abs"] = num("evaluation_reserve_margin_abs", 5.0)
    evaluation["reserve_margin_share"] = num("evaluation_reserve_margin_share", 0.10)
    evaluation["storage_operating_cost_per_mwh_throughput"] = num(
        "evaluation_storage_operating_cost_per_mwh_throughput",
        0.0,
    )
    config["evaluation"] = deepcopy(evaluation)
    model_settings["evaluation"] = evaluation
    model_settings["auction"] = {
        "pwin_default": num("auction_pwin_default", 0.35),
        "pwin_min": num("auction_pwin_min", 0.08),
        "pwin_max": num("auction_pwin_max", 0.88),
        "serious_competitors_default": num("auction_serious_competitors_default", 3.0),
        "serious_competitors_min": num("auction_serious_competitors_min", 2.0),
        "serious_competitors_max": num("auction_serious_competitors_max", 7.0),
        "rank_weight": num("auction_rank_weight", 0.22),
        "synergy_weight": num("auction_synergy_weight", 0.12),
        "scarcity_weight": num("auction_scarcity_weight", 0.08),
        "scope_weight": num("auction_scope_weight", 0.06),
        "safe_multiplier": num("auction_safe_multiplier", 0.82),
        "balanced_multiplier": num("auction_balanced_multiplier", 1.0),
        "aggressive_multiplier": num("auction_aggressive_multiplier", 1.22),
        "volatility_lambda_bid": num("auction_volatility_lambda_bid", 0.15),
        "auction_bid_model_version": text("auction_bid_model_version", "strategic_anchor_v4"),
        "conservative_utility_method": text(
            "auction_conservative_utility_method", "weighted_expected_minus_volatility"
        ),
    }

    return {"config_json": config, "model_settings": model_settings}


def strategy_profile_matrix() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for strategy_code in DEFAULT_STRATEGY_PROFILES:
        rows.append(
            {
                "code": strategy_code,
                "weights": [
                    {"key": weight_key, "label": weight_key} for weight_key in DEFAULT_WEIGHTS
                ],
            }
        )
    return rows
