from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..domain.ies2026 import EnergyObject, validate_network
from ..web.extensions import db
from ..web.models import GameSession, ObjectInstance, ObjectType
from ..web.services.purchased_objects import refresh_integration_state
from ..web.services.stale import mark_stale_for_session


def list_session_objects(session_id: int) -> List[ObjectInstance]:
    return (
        db.session.query(ObjectInstance)
        .filter_by(session_id=int(session_id))
        .order_by(ObjectInstance.created_at.desc(), ObjectInstance.id.desc())
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


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _canonical_code(code: str) -> str:
    normalized = _norm(code)
    if normalized in {"mini_substation_a", "mini_substation_b", "mini_substation", "mini"}:
        return "mini_substation"
    if normalized in {"house", "housea", "house_a"}:
        return "house_a"
    if normalized in {"houseb", "house_b"}:
        return "house_b"
    if normalized in {"cyber_solar", "solarrobot"}:
        return "solar"
    if normalized == "tps":
        return "wind"
    return normalized


def _normalize_parent(
    *,
    session_id: int,
    parent_instance_id: Any,
    current_object_id: int | None = None,
) -> int | None:
    if parent_instance_id in (None, "", 0, "0"):
        return None
    try:
        parent_id = int(float(parent_instance_id))
    except Exception as exc:
        raise ValueError("Родительский объект должен быть числом") from exc
    if current_object_id is not None and parent_id == int(current_object_id):
        raise ValueError("Объект не может быть родителем самому себе")
    parent = db.session.get(ObjectInstance, parent_id)
    if parent is None or int(parent.session_id) != int(session_id):
        raise ValueError("Родительский объект не найден в этой сессии")
    return parent_id


def _normalize_connection_inputs(
    *,
    session_id: int,
    parameters: Dict[str, Any],
    current_object_id: int | None,
) -> Dict[str, Any]:
    params = deepcopy(parameters)
    normalized_inputs: List[Dict[str, Any]] = []
    for idx, raw in enumerate(list(params.get("connection_inputs") or []), start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"connection_inputs[{idx}] должен быть объектом")
        parent_id = _normalize_parent(
            session_id=session_id,
            parent_instance_id=raw.get("parent_instance_id"),
            current_object_id=current_object_id,
        )
        normalized_inputs.append(
            {
                "key": str(raw.get("key") or f"in{idx}"),
                "label": str(raw.get("label") or f"Ввод {idx}"),
                "required": bool(raw.get("required", True)),
                "parent_instance_id": parent_id,
                "connection_point": str(
                    raw.get("connection_point") or raw.get("point") or raw.get("slot") or "A"
                ).upper(),
                "load_share": float(raw.get("load_share", 1.0) or 1.0),
            }
        )
    if normalized_inputs:
        params["connection_inputs"] = normalized_inputs

    if "secondary_parent_instance_id" in params:
        params["secondary_parent_instance_id"] = _normalize_parent(
            session_id=session_id,
            parent_instance_id=params.get("secondary_parent_instance_id"),
            current_object_id=current_object_id,
        )
    return params


def _normalize_parameters(
    session_id: int,
    value: Any,
    *,
    current_object_id: int | None = None,
) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("current_parameters должен быть объектом")
    return _normalize_connection_inputs(
        session_id=session_id,
        parameters=dict(value),
        current_object_id=current_object_id,
    )


def _energy_object(
    *,
    object_id: int,
    object_type: ObjectType,
    custom_name: str,
    parameters: Dict[str, Any],
    district: str,
    source_lot_id: Any,
    is_active: bool,
    parent_instance_id: int | None,
) -> EnergyObject:
    return EnergyObject(
        object_id=f"obj-{int(object_id)}",
        object_type_id=int(object_type.id),
        code=_canonical_code(object_type.code),
        name=custom_name or object_type.name,
        category=str(object_type.category),
        district=str(district or parameters.get("district") or "default"),
        parameters=deepcopy(parameters),
        source_lot_id=int(source_lot_id) if source_lot_id else None,
        is_candidate=False,
        is_active=bool(is_active),
        parent_id=f"obj-{int(parent_instance_id)}" if parent_instance_id else None,
        terminals=[],
    )


def _validate_future_state(
    *,
    session: GameSession,
    object_type: ObjectType,
    custom_name: str,
    parameters: Dict[str, Any],
    district: str,
    source_lot_id: Any,
    is_active: bool,
    parent_instance_id: int | None,
    current_object_id: int | None,
) -> None:
    future: List[EnergyObject] = []
    for row in session.objects:
        if current_object_id is not None and int(row.id) == int(current_object_id):
            future.append(
                _energy_object(
                    object_id=int(row.id),
                    object_type=object_type,
                    custom_name=custom_name,
                    parameters=parameters,
                    district=district,
                    source_lot_id=source_lot_id,
                    is_active=is_active,
                    parent_instance_id=parent_instance_id,
                )
            )
            continue
        if row.object_type is None:
            continue
        future.append(
            _energy_object(
                object_id=int(row.id),
                object_type=row.object_type,
                custom_name=row.custom_name,
                parameters=dict(row.current_parameters_json or {}),
                district=str(row.district or "default"),
                source_lot_id=row.source_lot_id,
                is_active=bool(row.is_active),
                parent_instance_id=int(row.parent_instance_id) if row.parent_instance_id else None,
            )
        )
    if current_object_id is None:
        future.append(
            _energy_object(
                object_id=-1,
                object_type=object_type,
                custom_name=custom_name,
                parameters=parameters,
                district=district,
                source_lot_id=source_lot_id,
                is_active=is_active,
                parent_instance_id=parent_instance_id,
            )
        )
    report = validate_network(future)
    critical = [issue.message for issue in report.issues if issue.severity == "critical"]
    if critical:
        raise ValueError(critical[0])


def create_session_object(payload: Dict[str, Any]) -> ObjectInstance:
    session_id = int(payload.get("session_id", 0) or 0)
    object_type_id = int(payload.get("object_type_id", 0) or 0)
    if session_id <= 0 or object_type_id <= 0:
        raise ValueError("session_id и object_type_id обязательны")

    session = _session_or_error(session_id)
    object_type = _object_type_or_error(object_type_id)
    parent_instance_id = _normalize_parent(
        session_id=session.id,
        parent_instance_id=payload.get("parent_instance_id"),
    )
    is_active = bool(payload.get("is_active", True))
    parameters = _normalize_parameters(session.id, payload.get("current_parameters"))
    custom_name = str(payload.get("custom_name", ""))
    district = str(payload.get("district", "default"))

    _validate_future_state(
        session=session,
        object_type=object_type,
        custom_name=custom_name,
        parameters=parameters,
        district=district,
        source_lot_id=payload.get("source_lot_id"),
        is_active=is_active,
        parent_instance_id=parent_instance_id,
        current_object_id=None,
    )

    row = ObjectInstance(
        session_id=session.id,
        object_type_id=object_type.id,
        custom_name=custom_name,
        current_parameters_json=parameters,
        source_lot_id=payload.get("source_lot_id"),
        is_from_start_pack=bool(payload.get("is_from_start_pack", False)),
        parent_instance_id=parent_instance_id,
        district=district,
        is_active=is_active,
    )
    refresh_integration_state(row)
    db.session.add(row)
    db.session.commit()
    mark_stale_for_session(session.id, reason="object_changed")
    return row


def update_session_object(row: ObjectInstance, payload: Dict[str, Any]) -> ObjectInstance:
    next_object_type = row.object_type or _object_type_or_error(int(row.object_type_id))
    if "object_type_id" in payload:
        next_object_type = _object_type_or_error(int(payload["object_type_id"]))
    next_parent_instance_id = int(row.parent_instance_id) if row.parent_instance_id is not None else None
    if "parent_instance_id" in payload:
        next_parent_instance_id = _normalize_parent(
            session_id=row.session_id,
            parent_instance_id=payload.get("parent_instance_id"),
            current_object_id=row.id,
        )
    next_parameters = dict(row.current_parameters_json or {})
    if "current_parameters" in payload:
        next_parameters = _normalize_parameters(
            row.session_id,
            payload["current_parameters"],
            current_object_id=int(row.id),
        )
    next_is_active = bool(payload["is_active"]) if "is_active" in payload else bool(row.is_active)
    next_custom_name = str(payload.get("custom_name", row.custom_name or ""))
    next_district = str(payload.get("district", row.district or "default"))

    _validate_future_state(
        session=row.session,
        object_type=next_object_type,
        custom_name=next_custom_name,
        parameters=next_parameters,
        district=next_district,
        source_lot_id=row.source_lot_id,
        is_active=next_is_active,
        parent_instance_id=next_parent_instance_id,
        current_object_id=int(row.id),
    )

    row.object_type_id = int(next_object_type.id)
    row.custom_name = next_custom_name
    row.district = next_district
    row.parent_instance_id = next_parent_instance_id
    row.current_parameters_json = next_parameters
    row.is_active = next_is_active
    refresh_integration_state(row)
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
        refresh_integration_state(child)
        db.session.add(child)
    db.session.delete(row)
    db.session.commit()
    mark_stale_for_session(session_id, reason="object_changed")
    return summary
