from __future__ import annotations

from typing import Any, Dict, List

from ..web.extensions import db
from ..web.models import GameSession, ObjectInstance, ObjectType
from ..web.services.stale import mark_stale_for_session


def list_session_objects(session_id: int) -> List[ObjectInstance]:
    return (
        db.session.query(ObjectInstance)
        .filter_by(session_id=int(session_id))
        .order_by(ObjectInstance.id)
        .all()
    )


def get_session_object_or_error(object_id: int) -> ObjectInstance:
    row = db.session.get(ObjectInstance, int(object_id))
    if row is None:
        raise ValueError(f"Объект {object_id} не найден")
    return row


def _session_or_error(session_id: int) -> GameSession:
    row = db.session.get(GameSession, int(session_id))
    if row is None:
        raise ValueError(f"Сессия {session_id} не найдена")
    return row


def _object_type_or_error(object_type_id: int) -> ObjectType:
    row = db.session.get(ObjectType, int(object_type_id))
    if row is None:
        raise ValueError(f"Тип объекта {object_type_id} не найден")
    if not row.is_active:
        raise ValueError(f"Тип объекта {row.code} неактивен")
    return row


def _normalize_parent(
    *,
    session_id: int,
    parent_instance_id: Any,
    current_object_id: int | None = None,
) -> int | None:
    if parent_instance_id in (None, "", 0, "0"):
        return None
    parent_id = int(parent_instance_id)
    if current_object_id is not None and parent_id == int(current_object_id):
        raise ValueError("Объект не может быть родителем самому себе")
    parent = db.session.get(ObjectInstance, parent_id)
    if parent is None or int(parent.session_id) != int(session_id):
        raise ValueError("Родительский объект не найден в этой сессии")
    return parent_id


def _normalize_parameters(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("current_parameters должен быть объектом")
    return dict(value)


def create_session_object(payload: Dict[str, Any]) -> ObjectInstance:
    session_id = int(payload.get("session_id", 0) or 0)
    object_type_id = int(payload.get("object_type_id", 0) or 0)
    if session_id <= 0 or object_type_id <= 0:
        raise ValueError("session_id и object_type_id обязательны")

    session = _session_or_error(session_id)
    object_type = _object_type_or_error(object_type_id)
    row = ObjectInstance(
        session_id=session.id,
        object_type_id=object_type.id,
        custom_name=str(payload.get("custom_name", "")),
        current_parameters_json=_normalize_parameters(payload.get("current_parameters")),
        source_lot_id=payload.get("source_lot_id"),
        is_from_start_pack=bool(payload.get("is_from_start_pack", False)),
        parent_instance_id=_normalize_parent(
            session_id=session.id,
            parent_instance_id=payload.get("parent_instance_id"),
        ),
        district=str(payload.get("district", "default")),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(row)
    db.session.commit()
    mark_stale_for_session(session.id, reason="object_changed")
    return row


def update_session_object(row: ObjectInstance, payload: Dict[str, Any]) -> ObjectInstance:
    if "object_type_id" in payload:
        object_type = _object_type_or_error(int(payload["object_type_id"]))
        row.object_type_id = object_type.id
    for key in ("custom_name", "district"):
        if key in payload:
            setattr(row, key, str(payload[key] or ""))
    if "parent_instance_id" in payload:
        row.parent_instance_id = _normalize_parent(
            session_id=row.session_id,
            parent_instance_id=payload.get("parent_instance_id"),
            current_object_id=row.id,
        )
    if "current_parameters" in payload:
        row.current_parameters_json = _normalize_parameters(payload["current_parameters"])
    if "is_active" in payload:
        row.is_active = bool(payload["is_active"])
    db.session.add(row)
    db.session.commit()
    mark_stale_for_session(row.session_id, reason="object_changed")
    return row


def delete_session_object(row: ObjectInstance) -> Dict[str, Any]:
    session_id = int(row.session_id)
    summary = {
        "object_id": int(row.id),
        "custom_name": row.custom_name,
        "children_count": row.children.count(),
    }
    for child in list(row.children):
        child.parent_instance_id = None
        db.session.add(child)
    db.session.delete(row)
    db.session.commit()
    mark_stale_for_session(session_id, reason="object_changed")
    return summary
