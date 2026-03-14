from __future__ import annotations

from typing import Any, Dict, List

from ..extensions import db
from ..models import ObjectType
from .stale import mark_stale_for_object_type


def list_object_types(*, include_inactive: bool = False) -> List[ObjectType]:
    query = db.session.query(ObjectType)
    if not include_inactive:
        query = query.filter_by(is_active=True)
    return query.order_by(ObjectType.code).all()


def get_object_type_or_error(object_type_id: int) -> ObjectType:
    row = db.session.get(ObjectType, int(object_type_id))
    if row is None:
        raise ValueError(f"ObjectType {object_type_id} not found")
    return row


def _dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} должен быть объектом")
    return dict(value)


def _list(value: Any, *, field_name: str) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} должен быть массивом")
    return list(value)


def _str(value: Any, *, field_name: str, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        raise ValueError(f"{field_name} должен быть строкой")
    return str(value).strip()


def create_object_type(payload: Dict[str, Any]) -> ObjectType:
    code = str(payload.get("code", "")).strip()
    name = str(payload.get("name", "")).strip()
    if not code or not name:
        raise ValueError("code и name обязательны")

    if db.session.query(ObjectType).filter_by(code=code).first() is not None:
        raise ValueError(f"ObjectType code={code} уже существует")

    row = ObjectType(
        code=code,
        name=name,
        category=str(payload.get("category", "infrastructure")),
        subtype=str(payload.get("subtype", "")),
        description=str(payload.get("description", "")),
        default_parameters_json=_dict(
            payload.get("default_parameters"),
            field_name="default_parameters",
        ),
        editable_fields_json=_list(payload.get("editable_fields"), field_name="editable_fields"),
        rules_json=_dict(payload.get("rules"), field_name="rules"),
        forecast_profile_key=_str(
            payload.get("forecast_profile_key"),
            field_name="forecast_profile_key",
            default="",
        ),
        resource_dependencies_json=_list(
            payload.get("resource_dependencies"),
            field_name="resource_dependencies",
        ),
        forecast_model_type=_str(
            payload.get("forecast_model_type"),
            field_name="forecast_model_type",
            default="direct_profile",
        )
        or "direct_profile",
        economic_role=_str(
            payload.get("economic_role"),
            field_name="economic_role",
            default="auto",
        )
        or "auto",
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(row)
    db.session.commit()
    return row


def update_object_type(row: ObjectType, payload: Dict[str, Any]) -> ObjectType:
    for key in ("name", "category", "subtype", "description"):
        if key in payload:
            setattr(row, key, str(payload.get(key) or ""))

    if "default_parameters" in payload:
        row.default_parameters_json = _dict(
            payload.get("default_parameters"),
            field_name="default_parameters",
        )
    if "editable_fields" in payload:
        row.editable_fields_json = _list(
            payload.get("editable_fields"),
            field_name="editable_fields",
        )
    if "rules" in payload:
        row.rules_json = _dict(payload.get("rules"), field_name="rules")
    if "forecast_profile_key" in payload:
        row.forecast_profile_key = _str(
            payload.get("forecast_profile_key"),
            field_name="forecast_profile_key",
            default="",
        )
    if "resource_dependencies" in payload:
        row.resource_dependencies_json = _list(
            payload.get("resource_dependencies"),
            field_name="resource_dependencies",
        )
    if "forecast_model_type" in payload:
        row.forecast_model_type = (
            _str(
                payload.get("forecast_model_type"),
                field_name="forecast_model_type",
                default="direct_profile",
            )
            or "direct_profile"
        )
    if "economic_role" in payload:
        row.economic_role = (
            _str(
                payload.get("economic_role"),
                field_name="economic_role",
                default="auto",
            )
            or "auto"
        )
    if "is_active" in payload:
        row.is_active = bool(payload.get("is_active"))

    db.session.add(row)
    db.session.commit()
    mark_stale_for_object_type(row.id, reason="object_type_changed")
    return row


def deactivate_object_type(row: ObjectType) -> ObjectType:
    row.is_active = False
    db.session.add(row)
    db.session.commit()
    mark_stale_for_object_type(row.id, reason="object_type_deactivated")
    return row
