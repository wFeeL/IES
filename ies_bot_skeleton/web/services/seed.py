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

DEFAULT_START_PACK_TEMPLATE_CODE = "core_default_2026"
DEFAULT_START_PACK_TEMPLATE_NAME = "Стартовый пакет 2026"
DEFAULT_START_PACK_TEMPLATE_DESCRIPTION = (
    "Главная подстанция, мини-подстанция и базовый жилой контур по правилам ИЭС 2026."
)
START_PACK_TEMPLATE_ITEMS_SEED: List[Dict[str, Any]] = [
    {
        "key": "main",
        "parent_key": None,
        "object_type_code": "main_substation",
        "quantity": 1,
        "custom_name": "Главная подстанция",
        "district": "trunk",
        "parameters_json": {},
        "sort_order": 10,
    },
    {
        "key": "mini_load",
        "parent_key": "main",
        "object_type_code": "mini_substation",
        "quantity": 1,
        "custom_name": "Мини-подстанция нагрузки",
        "district": "load_north",
        "parameters_json": {},
        "sort_order": 20,
    },
    {
        "key": "house_a",
        "parent_key": "mini_load",
        "object_type_code": "house_a",
        "quantity": 1,
        "custom_name": "Дом типа A",
        "district": "load_north",
        "parameters_json": {"connection_point": "B"},
        "sort_order": 30,
    },
]

OBJECT_TYPE_SEED: List[Dict[str, Any]] = [
    {
        "code": "house_a",
        "name": "Дом типа A",
        "category": "consumer",
        "subtype": "residential_a",
        "description": "Бытовой потребитель с умеренной эластичностью по тарифу.",
        "default_parameters_json": {
            "tariff_rub_per_tick": 6.2,
            "tariff_rub_per_mw_tick": 6.2,
            "expected_consumption_mw": 3.6,
            "elasticity": 0.18,
            "forecast_sensitivity": 1.0,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "tariff_rub_per_tick",
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "elasticity",
            "forecast_sensitivity",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "house_a_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "house_b",
        "name": "Дом типа B",
        "category": "consumer",
        "subtype": "residential_b",
        "description": "Потребитель с более высокой базовой нагрузкой и слабее выраженной эластичностью.",
        "default_parameters_json": {
            "tariff_rub_per_tick": 6.8,
            "tariff_rub_per_mw_tick": 6.8,
            "expected_consumption_mw": 4.4,
            "elasticity": 0.16,
            "forecast_sensitivity": 1.02,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "tariff_rub_per_tick",
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "elasticity",
            "forecast_sensitivity",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "house_b_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "office",
        "name": "Офис",
        "category": "consumer",
        "subtype": "office",
        "description": "Коммерческий потребитель с дневным профилем нагрузки.",
        "default_parameters_json": {
            "tariff_rub_per_tick": 7.5,
            "tariff_rub_per_mw_tick": 7.5,
            "expected_consumption_mw": 5.8,
            "elasticity": 0.12,
            "forecast_sensitivity": 1.0,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "tariff_rub_per_tick",
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "elasticity",
            "forecast_sensitivity",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "office_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "factory",
        "name": "Завод",
        "category": "consumer",
        "subtype": "industrial",
        "description": "Промышленный потребитель с одной или двумя точками подключения.",
        "default_parameters_json": {
            "tariff_rub_per_tick": 8.2,
            "tariff_rub_per_mw_tick": 8.2,
            "expected_consumption_mw": 13.0,
            "elasticity": 0.08,
            "forecast_sensitivity": 1.08,
            "connection_point": "A",
            "secondary_connection_point": "B",
        },
        "editable_fields_json": [
            "tariff_rub_per_tick",
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "elasticity",
            "forecast_sensitivity",
            "connection_point",
            "secondary_parent_instance_id",
            "secondary_connection_point",
        ],
        "rules_json": {"requires_substation": True, "supports_dual_input": True},
        "forecast_profile_key": "factory_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "hospital",
        "name": "Больница",
        "category": "consumer",
        "subtype": "critical",
        "description": "Критически важный потребитель с обязательным подключением двумя вводами.",
        "default_parameters_json": {
            "tariff_rub_per_tick": 9.8,
            "tariff_rub_per_mw_tick": 9.8,
            "expected_consumption_mw": 10.5,
            "elasticity": 0.03,
            "forecast_sensitivity": 1.0,
            "connection_point": "A",
            "secondary_connection_point": "B",
        },
        "editable_fields_json": [
            "tariff_rub_per_tick",
            "tariff_rub_per_mw_tick",
            "expected_consumption_mw",
            "elasticity",
            "connection_point",
            "secondary_parent_instance_id",
            "secondary_connection_point",
        ],
        "rules_json": {"requires_substation": True, "dual_input_required": True},
        "forecast_profile_key": "hospital_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "solar",
        "name": "СЭС",
        "category": "generator",
        "subtype": "solar",
        "description": "Солнечная электростанция с почти линейной зависимостью от освещённости.",
        "default_parameters_json": {
            "contract_rub_per_tick": 6.5,
            "generation_mw": 20.0,
            "efficiency": 0.94,
            "forecast_sensitivity": 1.0,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "efficiency",
            "forecast_sensitivity",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "illumination_profile",
        "resource_dependencies_json": ["illumination"],
        "forecast_model_type": "solar_output_2026",
        "economic_role": "generator",
    },
    {
        "code": "wind",
        "name": "ВЭС",
        "category": "generator",
        "subtype": "wind",
        "description": "Ветровая электростанция с параметризуемой кривой мощности и отдельным каналом ветра.",
        "default_parameters_json": {
            "contract_rub_per_tick": 7.1,
            "generation_mw": 18.0,
            "wind_channel": "wind_main",
            "cut_in_mps": 3.0,
            "rated_mps": 11.0,
            "cut_out_mps": 25.0,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "generation_mw",
            "wind_channel",
            "cut_in_mps",
            "rated_mps",
            "cut_out_mps",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "wind_main",
        "resource_dependencies_json": ["wind_channels"],
        "forecast_model_type": "wind_output_2026",
        "economic_role": "generator",
    },
    {
        "code": "storage",
        "name": "Накопитель",
        "category": "storage",
        "subtype": "battery",
        "description": "Накопитель с жёсткими лимитами мощности и ёмкости по правилам 2026.",
        "default_parameters_json": {
            "contract_rub_per_tick": 5.4,
            "capacity_mw_tick": 120.0,
            "charge_rate_mw_tick": 15.0,
            "discharge_rate_mw_tick": 20.0,
            "roundtrip_efficiency": 0.93,
            "connection_point": "A",
        },
        "editable_fields_json": [
            "contract_rub_per_tick",
            "capacity_mw_tick",
            "charge_rate_mw_tick",
            "discharge_rate_mw_tick",
            "roundtrip_efficiency",
            "connection_point",
        ],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "storage_dispatch_2026",
        "economic_role": "storage",
    },
    {
        "code": "main_substation",
        "name": "Главная подстанция",
        "category": "infrastructure",
        "subtype": "main",
        "description": "Корневой обязательный узел энергосистемы.",
        "default_parameters_json": {"ports": 8, "contract_rub_per_tick": 0.0, "district": "trunk"},
        "editable_fields_json": ["ports", "district", "contract_rub_per_tick"],
        "rules_json": {"requires_substation": False, "is_root": True},
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "network_root_2026",
        "economic_role": "infrastructure",
    },
    {
        "code": "mini_substation",
        "name": "Мини-подстанция",
        "category": "infrastructure",
        "subtype": "distribution",
        "description": "Распределительный узел, который расширяет дерево сети и может открывать ценность других объектов.",
        "default_parameters_json": {"ports": 5, "contract_rub_per_tick": 4.0, "district": "default"},
        "editable_fields_json": ["ports", "district", "contract_rub_per_tick"],
        "rules_json": {"requires_substation": True},
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "network_enabler_2026",
        "economic_role": "infrastructure",
    },
]

OBJECT_TYPE_ALIAS_SEED: List[Dict[str, Any]] = [
    {
        "code": "house",
        "name": "Совместимость: house",
        "category": "consumer",
        "subtype": "compat_house_a",
        "description": "Совместимый алиас для старых сценариев. Канонизируется в house_a.",
        "default_parameters_json": dict(OBJECT_TYPE_SEED[0]["default_parameters_json"]),
        "editable_fields_json": list(OBJECT_TYPE_SEED[0]["editable_fields_json"]),
        "rules_json": {"compatibility_alias": True, "canonical_code": "house_a", "hidden_from_ui": True},
        "forecast_profile_key": "house_a_load",
        "resource_dependencies_json": [],
        "forecast_model_type": "consumer_profile_2026",
        "economic_role": "consumer",
    },
    {
        "code": "mini_substation_a",
        "name": "Совместимость: mini_substation_a",
        "category": "infrastructure",
        "subtype": "compat_mini",
        "description": "Совместимый алиас для старых сценариев. Канонизируется в mini_substation.",
        "default_parameters_json": dict(OBJECT_TYPE_SEED[-1]["default_parameters_json"]),
        "editable_fields_json": list(OBJECT_TYPE_SEED[-1]["editable_fields_json"]),
        "rules_json": {"compatibility_alias": True, "canonical_code": "mini_substation", "hidden_from_ui": True},
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "network_enabler_2026",
        "economic_role": "infrastructure",
    },
    {
        "code": "mini_substation_b",
        "name": "Совместимость: mini_substation_b",
        "category": "infrastructure",
        "subtype": "compat_mini",
        "description": "Совместимый алиас для старых сценариев. Канонизируется в mini_substation.",
        "default_parameters_json": dict(OBJECT_TYPE_SEED[-1]["default_parameters_json"]),
        "editable_fields_json": list(OBJECT_TYPE_SEED[-1]["editable_fields_json"]),
        "rules_json": {"compatibility_alias": True, "canonical_code": "mini_substation", "hidden_from_ui": True},
        "forecast_profile_key": "",
        "resource_dependencies_json": [],
        "forecast_model_type": "network_enabler_2026",
        "economic_role": "infrastructure",
    },
    {
        "code": "cyber_solar",
        "name": "Совместимость: cyber_solar",
        "category": "generator",
        "subtype": "compat_solar",
        "description": "Совместимый алиас для старых сценариев. Канонизируется в solar.",
        "default_parameters_json": dict(OBJECT_TYPE_SEED[5]["default_parameters_json"]),
        "editable_fields_json": list(OBJECT_TYPE_SEED[5]["editable_fields_json"]),
        "rules_json": {"compatibility_alias": True, "canonical_code": "solar", "hidden_from_ui": True},
        "forecast_profile_key": "illumination_profile",
        "resource_dependencies_json": ["illumination"],
        "forecast_model_type": "solar_output_2026",
        "economic_role": "generator",
    },
    {
        "code": "tps",
        "name": "Совместимость: tps",
        "category": "generator",
        "subtype": "compat_dispatchable_generation",
        "description": "Совместимый алиас для старых сценариев. Внутри 2026-движка используется как параметризуемый генераторный прокси.",
        "default_parameters_json": dict(OBJECT_TYPE_SEED[6]["default_parameters_json"]),
        "editable_fields_json": list(OBJECT_TYPE_SEED[6]["editable_fields_json"]),
        "rules_json": {"compatibility_alias": True, "canonical_code": "wind", "hidden_from_ui": True},
        "forecast_profile_key": "wind_main",
        "resource_dependencies_json": ["wind_channels"],
        "forecast_model_type": "wind_output_2026",
        "economic_role": "generator",
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
        "evaluation": dict(cfg.get("evaluation", {}) or {}),
        "auction": dict(cfg.get("auction", {}) or {}),
        "market": dict(cfg.get("market", {}) or {}),
        "network": dict(cfg.get("network", {}) or {}),
    }


def _ensure_start_pack_template(
    *,
    type_map: Dict[str, ObjectType],
    code: str,
    name: str,
    description: str,
    items_seed: List[Dict[str, Any]],
) -> StartPackTemplate:
    template = db.session.query(StartPackTemplate).filter_by(code=code).one_or_none()
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
        type_row = type_map.get(str(item["object_type_code"]))
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
        row = key_to_row.get(str(item.get("key", "")))
        parent = key_to_row.get(str(item.get("parent_key", ""))) if item.get("parent_key") else None
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
    created = {"users": 0, "rulesets": 0, "object_types": 0, "start_pack_templates": 0}

    before_users = db.session.query(User).count()
    _ensure_user("admin", "admin", admin_password)
    _ensure_user("analyst", "analyst", analyst_password)
    db.session.flush()
    created["users"] = max(0, db.session.query(User).count() - before_users)

    base_rules_cfg = build_default_ruleset_config()
    default_model_settings = build_default_model_settings(base_rules_cfg)

    for code, version, name in (
        (GENERIC_RULESET_CODE, GENERIC_RULESET_VERSION, GENERIC_RULESET_NAME),
        (TEST_GAME_RULESET_CODE, TEST_GAME_RULESET_VERSION, TEST_GAME_RULESET_NAME),
    ):
        row = db.session.query(Ruleset).filter_by(code=code, version=version).one_or_none()
        if row is None:
            row = Ruleset(
                code=code,
                version=version,
                name=name,
                config_json=base_rules_cfg,
                model_settings_json=default_model_settings,
                is_builtin=True,
                is_active=True,
            )
            db.session.add(row)
            created["rulesets"] += 1
        else:
            row.name = name
            row.config_json = base_rules_cfg
            row.model_settings_json = default_model_settings
            row.is_builtin = True
            row.is_active = True
            db.session.add(row)

    all_object_type_seed = [*OBJECT_TYPE_SEED, *OBJECT_TYPE_ALIAS_SEED]
    seeded_codes = {row["code"] for row in all_object_type_seed}
    for seed in all_object_type_seed:
        row = db.session.query(ObjectType).filter_by(code=seed["code"]).one_or_none()
        if row is None:
            db.session.add(ObjectType(**seed, is_active=True))
            created["object_types"] += 1
            continue
        row.name = seed["name"]
        row.category = seed["category"]
        row.subtype = seed["subtype"]
        row.description = seed["description"]
        row.default_parameters_json = seed["default_parameters_json"]
        row.editable_fields_json = seed["editable_fields_json"]
        row.rules_json = seed["rules_json"]
        row.forecast_profile_key = seed.get("forecast_profile_key", "")
        row.resource_dependencies_json = list(seed.get("resource_dependencies_json", []) or [])
        row.forecast_model_type = seed.get("forecast_model_type", "direct_profile")
        row.economic_role = seed.get("economic_role", "auto")
        row.is_active = True
        db.session.add(row)

    for legacy in db.session.query(ObjectType).all():
        if legacy.code not in seeded_codes:
            legacy.is_active = False
            db.session.add(legacy)

    type_map = {row.code: row for row in db.session.query(ObjectType).all()}
    before_templates = db.session.query(StartPackTemplate).count()
    default_template = _ensure_default_start_pack_template(type_map)
    test_game_template = _ensure_test_game_start_pack_template(type_map)
    after_templates = db.session.query(StartPackTemplate).count()
    created["start_pack_templates"] = max(0, after_templates - before_templates)

    for code, version, template in (
        (GENERIC_RULESET_CODE, GENERIC_RULESET_VERSION, default_template),
        (TEST_GAME_RULESET_CODE, TEST_GAME_RULESET_VERSION, test_game_template),
    ):
        ruleset = db.session.query(Ruleset).filter_by(code=code, version=version).one()
        if ruleset.active_start_pack_template_id != template.id:
            ruleset.active_start_pack_template_id = template.id
            db.session.add(ruleset)

    db.session.commit()
    return created
