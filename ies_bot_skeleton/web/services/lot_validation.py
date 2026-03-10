from __future__ import annotations

from typing import Any, Dict, List

from ..extensions import db
from ..models import ObjectType


def _resolve_object_type(row: Dict[str, Any]) -> ObjectType | None:
    object_type_id = row.get("object_type_id")
    if object_type_id is not None:
        try:
            return db.session.get(ObjectType, int(object_type_id))
        except Exception:
            return None

    object_type_code = row.get("object_type_code")
    if object_type_code is None:
        return None
    return db.session.query(ObjectType).filter_by(code=str(object_type_code)).one_or_none()


def validate_and_normalize_lot_items(
    items_payload: Any,
    *,
    require_active_type: bool = True,
) -> List[Dict[str, Any]]:
    if not isinstance(items_payload, list):
        raise ValueError("items должен быть массивом")

    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(items_payload, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{idx}] должен быть объектом")

        obj_type = _resolve_object_type(raw)
        if obj_type is None:
            raise ValueError(f"items[{idx}] содержит неизвестный object type")
        if require_active_type and not bool(obj_type.is_active):
            raise ValueError(f"items[{idx}] использует неактивный object type {obj_type.code}")

        quantity_raw = raw.get("quantity", 1)
        if quantity_raw is None:
            quantity_raw = 1
        try:
            quantity = int(quantity_raw)
        except Exception as exc:
            raise ValueError(f"items[{idx}].quantity должен быть целым") from exc
        if quantity < 1:
            raise ValueError(f"items[{idx}].quantity должен быть >= 1")

        overrides = raw.get("overrides", {})
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(f"items[{idx}].overrides должен быть объектом")

        normalized.append(
            {
                "object_type_id": int(obj_type.id),
                "object_type_code": obj_type.code,
                "quantity": quantity,
                "overrides": dict(overrides),
            }
        )

    if not normalized:
        raise ValueError("Лот не может быть пустым")
    return normalized
