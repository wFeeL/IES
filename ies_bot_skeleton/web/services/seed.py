from __future__ import annotations

from typing import Any, Dict, List

from ..extensions import db
from ..models import ObjectType, Ruleset, User
from .ruleset import build_default_ruleset_config

START_PACK_CODES = ["main_substation", "mini_substation_a", "cyber_solar", "house"]

OBJECT_TYPE_SEED: List[Dict[str, Any]] = [
    {
        "code": "house",
        "name": "Жилой дом",
        "category": "consumer",
        "subtype": "residential",
        "description": "Базовый потребитель класса house.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 14.0,
            "expected_consumption_mw": 8.0,
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
    },
    {
        "code": "office",
        "name": "Офис",
        "category": "consumer",
        "subtype": "office",
        "description": "Потребитель со средней нагрузкой.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 18.0,
            "expected_consumption_mw": 10.0,
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
    },
    {
        "code": "factory",
        "name": "Завод",
        "category": "consumer",
        "subtype": "industry",
        "description": "Промышленный потребитель с высоким штрафом за недоотпуск.",
        "default_parameters_json": {
            "tariff_rub_per_mw_tick": 25.0,
            "expected_consumption_mw": 20.0,
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
    },
    {
        "code": "cyber_solar",
        "name": "Кибер-СЭС",
        "category": "generator",
        "subtype": "solar",
        "description": "СЭС с зависимостью от освещенности.",
        "default_parameters_json": {
            "contract_rub_per_tick": 120.0,
            "generation_mw": 12.0,
            "depends_on_sun": True,
            "efficiency": 0.95,
            "eco_score": 3.0,
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
    },
    {
        "code": "solar",
        "name": "СЭС",
        "category": "generator",
        "subtype": "solar",
        "description": "Обычная солнечная генерация.",
        "default_parameters_json": {
            "contract_rub_per_tick": 110.0,
            "generation_mw": 10.0,
            "depends_on_sun": True,
            "efficiency": 0.92,
            "eco_score": 2.8,
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
    },
    {
        "code": "wind",
        "name": "ВЭС",
        "category": "generator",
        "subtype": "wind",
        "description": "Ветрогенератор с нелинейной зависимостью.",
        "default_parameters_json": {
            "contract_rub_per_tick": 120.0,
            "generation_mw": 14.0,
            "depends_on_wind": True,
            "efficiency": 0.9,
            "eco_score": 2.5,
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
    },
    {
        "code": "tps",
        "name": "ТЭС",
        "category": "generator",
        "subtype": "thermal",
        "description": "Управляемая генерация с топливом и налогами.",
        "default_parameters_json": {
            "contract_rub_per_tick": 140.0,
            "generation_mw": 16.0,
            "fuel_price": 0.5,
            "eco_tax_fuel": 1.5,
            "efficiency": 0.4,
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
    },
    {
        "code": "storage",
        "name": "Накопитель",
        "category": "storage",
        "subtype": "battery",
        "description": "Аккумулятор с емкостью и скоростью заряда/разряда.",
        "default_parameters_json": {
            "contract_rub_per_tick": 80.0,
            "capacity_mw_tick": 80.0,
            "charge_rate_mw": 15.0,
            "discharge_rate_mw": 20.0,
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
    },
    {
        "code": "main_substation",
        "name": "Главная подстанция",
        "category": "infrastructure",
        "subtype": "main",
        "description": "Корневой узел дерева сети.",
        "default_parameters_json": {
            "ports": 10,
            "requires_substation": False,
            "district": "core",
            "wear_impact": 0.1,
        },
        "editable_fields_json": ["ports", "district", "wear_impact"],
        "rules_json": {
            "requires_substation": False,
            "forbid_mixed_gen_load": False,
            "is_root": True,
        },
    },
    {
        "code": "mini_substation_a",
        "name": "Мини-подстанция A",
        "category": "infrastructure",
        "subtype": "miniA",
        "description": "Локальный узел сети с ограничением портов.",
        "default_parameters_json": {
            "ports": 3,
            "requires_substation": True,
            "district": "default",
            "wear_impact": 0.2,
        },
        "editable_fields_json": ["ports", "district", "wear_impact"],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": True,
        },
    },
    {
        "code": "mini_substation_b",
        "name": "Мини-подстанция B",
        "category": "infrastructure",
        "subtype": "miniB",
        "description": "Расширенный локальный узел сети.",
        "default_parameters_json": {
            "ports": 4,
            "requires_substation": True,
            "district": "default",
            "wear_impact": 0.2,
        },
        "editable_fields_json": ["ports", "district", "wear_impact"],
        "rules_json": {
            "requires_substation": True,
            "forbid_mixed_gen_load": True,
        },
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


def ensure_seed_data(
    *,
    admin_password: str = "admin123",
    analyst_password: str = "analyst123",
) -> Dict[str, int]:
    created = {
        "users": 0,
        "rulesets": 0,
        "object_types": 0,
    }

    before_users = db.session.query(User).count()
    _ensure_user("admin", "admin", admin_password)
    _ensure_user("analyst", "analyst", analyst_password)
    db.session.flush()
    created["users"] = max(0, db.session.query(User).count() - before_users)

    ruleset = db.session.query(Ruleset).filter_by(code="ies_2026", version="1").one_or_none()
    if ruleset is None:
        ruleset = Ruleset(
            code="ies_2026",
            version="1",
            name="IES Ruleset 2026",
            config_json=build_default_ruleset_config(),
            is_builtin=True,
            is_active=True,
        )
        db.session.add(ruleset)
        created["rulesets"] += 1

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
        current.is_active = True
        db.session.add(current)

    db.session.commit()
    return created
