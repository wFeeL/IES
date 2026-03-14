from __future__ import annotations

from typing import Any, Dict, List

from ..extensions import db
from ..models import ObjectType, Ruleset, StartPackTemplate, StartPackTemplateItem, User
from .ruleset import build_default_ruleset_config
from .test_game_preset import (
    TEST_GAME_RULESET_CODE,
    TEST_GAME_RULESET_NAME,
    TEST_GAME_RULESET_VERSION,
    TEST_GAME_START_PACK_CODE,
    TEST_GAME_START_PACK_DESCRIPTION,
    TEST_GAME_START_PACK_NAME,
)

GENERIC_RULESET_CODE = "ies_2026"
GENERIC_RULESET_VERSION = "1"
GENERIC_RULESET_NAME = "IES Ruleset 2026"
DEFAULT_START_PACK_TEMPLATE_CODE = "core_default"
DEFAULT_START_PACK_TEMPLATE_NAME = "Стартовый пакет аукциона"
DEFAULT_START_PACK_TEMPLATE_DESCRIPTION = (
    "Главная подстанция + мини-подстанция + солнечная панель + жилой дом."
)
START_PACK_TEMPLATE_ITEMS_SEED: List[Dict[str, Any]] = [
    {
        "key": "main",
        "parent_key": None,
        "object_type_code": "main_substation",
        "quantity": 1,
        "custom_name": "Главная подстанция",
        "district": "core",
        "parameters_json": {},
        "sort_order": 10,
    },
    {
        "key": "mini",
        "parent_key": "main",
        "object_type_code": "mini_substation_a",
        "quantity": 1,
        "custom_name": "Мини-подстанция",
        "district": "core",
        "parameters_json": {},
        "sort_order": 20,
    },
    {
        "key": "solar",
        "parent_key": "mini",
        "object_type_code": "cyber_solar",
        "quantity": 1,
        "custom_name": "Солнечная панель",
        "district": "core",
        "parameters_json": {"connection_point": "B"},
        "sort_order": 30,
    },
    {
        "key": "house",
        "parent_key": "mini",
        "object_type_code": "house",
        "quantity": 1,
        "custom_name": "Жилой дом",
        "district": "core",
        "parameters_json": {"connection_point": "C"},
        "sort_order": 40,
    },
]

OBJECT_TYPE_SEED: List[Dict[str, Any]] = [
    {
        "code": "house",
        "name": "Жилой дом",
        "category": "consumer",
        "subtype": "residential",
        "description": "Базовый бытовой потребитель тестовой игры.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 3.0,
            "expected_consumption_mw": 2.0,
            "requires_substation": True,
            "profile": "houseA",
            "eco_score": 0.0,
            "maintenance_cost": 0.0,
            "tax": 0.0,
            "forecast_sensitivity": 1.0,
        },
        "editable_fields_json": [
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "profile",
            "eco_score",
            "maintenance_cost",
            "tax",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "house_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "profile_scaled_load",
        "economic_role": "consumer",
    },
    {
        "code": "office",
        "name": "Офис",
        "category": "consumer",
        "subtype": "office",
        "description": "Коммерческий потребитель со средней нагрузкой.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 5.0,
            "expected_consumption_mw": 1.0,
            "requires_substation": True,
            "profile": "office",
            "eco_score": 0.0,
            "maintenance_cost": 0.0,
            "tax": 0.0,
            "forecast_sensitivity": 1.0,
        },
        "editable_fields_json": [
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "profile",
            "eco_score",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "office_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "profile_scaled_load",
        "economic_role": "consumer",
    },
    {
        "code": "factory",
        "name": "Завод",
        "category": "consumer",
        "subtype": "industry",
        "description": "Промышленный потребитель с высоким штрафом за недоотпуск.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 4.0,
            "expected_consumption_mw": 6.0,
            "requires_substation": True,
            "profile": "factory",
            "eco_score": -1.0,
            "maintenance_cost": 0.0,
            "tax": 0.0,
            "forecast_sensitivity": 1.1,
        },
        "editable_fields_json": [
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "profile",
            "eco_score",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "factory_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "profile_scaled_load",
        "economic_role": "consumer",
    },
    {
        "code": "cyber_solar",
        "name": "Солнечная панель",
        "category": "generator",
        "subtype": "solar",
        "description": "Солнечная генерация тестовой игры с бонусом ВИЭ.",
        "default_parameters_json": {
            "contract_rub_per_tick": 2.0,
            "generation_mw": 10.0,
            "depends_on_sun": True,
            "efficiency": 0.95,
            "eco_score": 2.0,
            "requires_substation": True,
            "forecast_sensitivity": 1.2,
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "efficiency",
            "eco_score",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "solar_profile",
        "resource_dependencies_json": ["solar_factor"],
        "forecast_model_type": "solar_factor_output",
        "economic_role": "generator",
    },
    {
        "code": "solar",
        "name": "СЭС",
        "category": "generator",
        "subtype": "solar",
        "description": "Обычная солнечная генерация.",
        "default_parameters_json": {
            "contract_rub_per_tick": 2.0,
            "generation_mw": 10.0,
            "depends_on_sun": True,
            "efficiency": 0.95,
            "eco_score": 2.0,
            "requires_substation": True,
            "forecast_sensitivity": 1.1,
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "efficiency",
            "eco_score",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "solar_profile",
        "resource_dependencies_json": ["solar_factor"],
        "forecast_model_type": "solar_factor_output",
        "economic_role": "generator",
    },
    {
        "code": "wind",
        "name": "Ветряк",
        "category": "generator",
        "subtype": "wind",
        "description": "Ветрогенератор тестовой игры с бонусом ВИЭ.",
        "default_parameters_json": {
            "contract_rub_per_tick": 1.0,
            "generation_mw": 8.0,
            "depends_on_wind": True,
            "efficiency": 0.9,
            "eco_score": 2.0,
            "requires_substation": True,
            "forecast_sensitivity": 1.0,
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "efficiency",
            "eco_score",
            "forecast_sensitivity",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "wind_profile",
        "resource_dependencies_json": ["wind_factor"],
        "forecast_model_type": "wind_factor_curve",
        "economic_role": "generator",
    },
    {
        "code": "tps",
        "name": "ТЭС",
        "category": "generator",
        "subtype": "thermal",
        "description": "Управляемая тепловая генерация с расходом топлива.",
        "default_parameters_json": {
            "contract_rub_per_tick": 0.0,
            "generation_mw": 15.0,
            "fuel_price": 0.5,
            "eco_tax_fuel": 0.0,
            "efficiency": 1.0,
            "eco_score": -2.0,
            "requires_substation": True,
            "forecast_sensitivity": 0.2,
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "fuel_price",
            "eco_tax_fuel",
            "efficiency",
            "eco_score",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "dispatchable_thermal",
        "economic_role": "generator",
    },
    {
        "code": "storage",
        "name": "Накопитель",
        "category": "storage",
        "subtype": "battery",
        "description": "Аккумулятор с потерями заряда и ограничением по мощности.",
        "default_parameters_json": {
            "contract_rub_per_tick": 3.0,
            "capacity_mw_tick": 20.0,
            "charge_rate_mw": 5.0,
            "discharge_rate_mw": 5.0,
            "efficiency": 0.95,
            "eco_score": 1.0,
            "requires_substation": True,
            "forecast_sensitivity": 0.3,
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "capacity_mw_tick",
            "charge_rate_mw",
            "discharge_rate_mw",
            "efficiency",
            "eco_score",
        ],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "storage_default_profile",
        "resource_dependencies_json": [],
        "forecast_model_type": "storage_dispatch",
        "economic_role": "storage",
    },
    {
        "code": "main_substation",
        "name": "Главная подстанция",
        "category": "infrastructure",
        "subtype": "main",
        "description": "Главный узел сети с тремя портами и лимитом по мощности.",
        "default_parameters_json": {
            "ports": 3,
            "contract_rub_per_tick": 1.0,
            "soft_flow_limit_mw": 40.0,
            "requires_substation": False,
            "district": "core",
            "wear_impact": 0.1,
        },
        "editable_fields_json": [
            "ports",
            "contract_rub_per_tick",
            "soft_flow_limit_mw",
            "district",
            "wear_impact",
        ],
        "rules_json": {
            "requires_substation": False,
            "forbid_mixed_gen_load": False,
            "is_root": True,
        },
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "infrastructure_constraint",
        "economic_role": "infrastructure",
    },
    {
        "code": "mini_substation_a",
        "name": "Мини-подстанция",
        "category": "infrastructure",
        "subtype": "miniA",
        "description": "Мини-подстанция на три порта для расширения сети.",
        "default_parameters_json": {
            "ports": 3,
            "requires_substation": True,
            "district": "default",
            "wear_impact": 0.2,
        },
        "editable_fields_json": ["ports", "district", "wear_impact"],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "infrastructure_constraint",
        "economic_role": "infrastructure",
    },
    {
        "code": "mini_substation_b",
        "name": "Мини-подстанция B",
        "category": "infrastructure",
        "subtype": "miniB",
        "description": "Расширенный локальный узел сети.",
        "default_parameters_json": {
            "ports": 3,
            "requires_substation": True,
            "district": "default",
            "wear_impact": 0.2,
        },
        "editable_fields_json": ["ports", "district", "wear_impact"],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": False,
        },
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "infrastructure_constraint",
        "economic_role": "infrastructure",
    },
]


def _ensure_user(username: str, role: str, password: str) -> None:
    user = db.session.query(User).filter_by(username=username).one_or_none()
    if user is None:
        user = User(username=username, role=role, is_active=True)
        user.set_password(password)
        db.session.add(user)
        return

    changed = False
    if user.role != role:
        user.role = role
        changed = True
    if not user.check_password(password):
        user.set_password(password)
        changed = True
    if changed:
        db.session.add(user)


def build_default_model_settings(config_json: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(config_json or {})
    return {
        "lot_score_weights": dict(cfg.get("lot_score_weights", {}) or {}),
        "strategy_profiles": dict(cfg.get("strategy_profiles", {}) or {}),
        "evaluation": dict(cfg.get("evaluation", {}) or {}),
        "auction": dict(cfg.get("auction", {}) or {}),
    }


def _ensure_start_pack_template(
    *,
    type_map: Dict[str, ObjectType],
    code: str,
    name: str,
    description: str,
    items_seed: List[Dict[str, Any]],
) -> StartPackTemplate:
    template = (
        db.session.query(StartPackTemplate)
        .filter_by(code=code)
        .one_or_none()
    )
    if template is None:
        template = StartPackTemplate(
            code=code,
            name=name,
            description=description,
            is_builtin=True,
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()
    else:
        template.name = name
        template.description = description
        template.is_builtin = True
        template.is_active = True
        db.session.add(template)
        db.session.flush()

    template.items.clear()
    db.session.flush()

    key_to_row: Dict[str, StartPackTemplateItem] = {}
    for item in items_seed:
        code = str(item["object_type_code"])
        type_row = type_map.get(code)
        if type_row is None:
            continue
        row = StartPackTemplateItem(
            template_id=template.id,
            object_type_id=type_row.id,
            quantity=max(1, int(item.get("quantity", 1) or 1)),
            custom_name=str(item.get("custom_name", "")),
            district=str(item.get("district", "core")),
            parameters_json=dict(item.get("parameters_json", {}) or {}),
            parent_item_id=None,
            sort_order=int(item.get("sort_order", 0) or 0),
            is_active=True,
        )
        db.session.add(row)
        db.session.flush()
        key_to_row[str(item.get("key", ""))] = row

    for item in items_seed:
        key = str(item.get("key", ""))
        parent_key = item.get("parent_key")
        if not parent_key:
            continue
        row = key_to_row.get(key)
        parent = key_to_row.get(str(parent_key))
        if row is None or parent is None:
            continue
        row.parent_item_id = parent.id
        db.session.add(row)
    return template


def _ensure_default_start_pack_template(type_map: Dict[str, ObjectType]) -> StartPackTemplate:
    return _ensure_start_pack_template(
        type_map=type_map,
        code=DEFAULT_START_PACK_TEMPLATE_CODE,
        name=DEFAULT_START_PACK_TEMPLATE_NAME,
        description=DEFAULT_START_PACK_TEMPLATE_DESCRIPTION,
        items_seed=START_PACK_TEMPLATE_ITEMS_SEED,
    )


def _ensure_test_game_start_pack_template(type_map: Dict[str, ObjectType]) -> StartPackTemplate:
    return _ensure_start_pack_template(
        type_map=type_map,
        code=TEST_GAME_START_PACK_CODE,
        name=TEST_GAME_START_PACK_NAME,
        description=TEST_GAME_START_PACK_DESCRIPTION,
        items_seed=START_PACK_TEMPLATE_ITEMS_SEED,
    )


def ensure_seed_data(
    *,
    admin_password: str = "admin123",
    analyst_password: str = "analyst123",
) -> Dict[str, int]:
    created = {
        "users": 0,
        "rulesets": 0,
        "object_types": 0,
        "start_pack_templates": 0,
    }

    before_users = db.session.query(User).count()
    _ensure_user("admin", "admin", admin_password)
    _ensure_user("analyst", "analyst", analyst_password)
    db.session.flush()
    created["users"] = max(0, db.session.query(User).count() - before_users)

    base_rules_cfg = build_default_ruleset_config()
    default_model_settings = build_default_model_settings(base_rules_cfg)

    generic_ruleset = (
        db.session.query(Ruleset)
        .filter_by(code=GENERIC_RULESET_CODE, version=GENERIC_RULESET_VERSION)
        .one_or_none()
    )
    if generic_ruleset is None:
        generic_ruleset = Ruleset(
            code=GENERIC_RULESET_CODE,
            version=GENERIC_RULESET_VERSION,
            name=GENERIC_RULESET_NAME,
            config_json=base_rules_cfg,
            model_settings_json=default_model_settings,
            is_builtin=True,
            is_active=True,
        )
        db.session.add(generic_ruleset)
        created["rulesets"] += 1
    else:
        generic_ruleset.name = GENERIC_RULESET_NAME
        generic_ruleset.config_json = base_rules_cfg
        if not isinstance(generic_ruleset.model_settings_json, dict):
            generic_ruleset.model_settings_json = default_model_settings
        generic_ruleset.is_builtin = True
        generic_ruleset.is_active = True
        db.session.add(generic_ruleset)

    test_game_ruleset = (
        db.session.query(Ruleset)
        .filter_by(code=TEST_GAME_RULESET_CODE, version=TEST_GAME_RULESET_VERSION)
        .one_or_none()
    )
    if test_game_ruleset is None:
        test_game_ruleset = Ruleset(
            code=TEST_GAME_RULESET_CODE,
            version=TEST_GAME_RULESET_VERSION,
            name=TEST_GAME_RULESET_NAME,
            config_json=base_rules_cfg,
            model_settings_json=default_model_settings,
            is_builtin=True,
            is_active=True,
        )
        db.session.add(test_game_ruleset)
        created["rulesets"] += 1
    else:
        test_game_ruleset.name = TEST_GAME_RULESET_NAME
        test_game_ruleset.config_json = base_rules_cfg
        if not isinstance(test_game_ruleset.model_settings_json, dict):
            test_game_ruleset.model_settings_json = default_model_settings
        test_game_ruleset.is_builtin = True
        test_game_ruleset.is_active = True
        db.session.add(test_game_ruleset)

    for row in OBJECT_TYPE_SEED:
        current = db.session.query(ObjectType).filter_by(code=row["code"]).one_or_none()
        if current is None:
            db.session.add(ObjectType(**row, is_active=True))
            created["object_types"] += 1
            continue

        current.name = row["name"]
        current.category = row["category"]
        current.subtype = row["subtype"]
        current.description = row["description"]
        current.default_parameters_json = row["default_parameters_json"]
        current.editable_fields_json = row["editable_fields_json"]
        current.rules_json = row["rules_json"]
        current.forecast_profile_key = row.get("forecast_profile_key", "")
        current.resource_dependencies_json = list(row.get("resource_dependencies_json", []) or [])
        current.forecast_model_type = row.get("forecast_model_type", "direct_profile")
        current.economic_role = row.get("economic_role", "auto")
        current.is_active = True
        db.session.add(current)

    type_map = {row.code: row for row in db.session.query(ObjectType).all()}
    before_templates = db.session.query(StartPackTemplate).count()
    default_template = _ensure_default_start_pack_template(type_map)
    test_game_template = _ensure_test_game_start_pack_template(type_map)
    after_templates = db.session.query(StartPackTemplate).count()
    created["start_pack_templates"] = max(0, after_templates - before_templates)

    if generic_ruleset.active_start_pack_template_id != default_template.id:
        generic_ruleset.active_start_pack_template_id = default_template.id
        db.session.add(generic_ruleset)
    if test_game_ruleset.active_start_pack_template_id != test_game_template.id:
        test_game_ruleset.active_start_pack_template_id = test_game_template.id
        db.session.add(test_game_ruleset)

    db.session.commit()
    return created
