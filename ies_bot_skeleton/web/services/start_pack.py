from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..extensions import db
from ..models import (
    GameSession,
    ObjectInstance,
    ObjectType,
    StartPackTemplate,
    StartPackTemplateItem,
)


def list_start_pack_templates(*, include_inactive: bool = False) -> List[StartPackTemplate]:
    query = db.session.query(StartPackTemplate)
    if not include_inactive:
        query = query.filter_by(is_active=True)
    return query.order_by(StartPackTemplate.created_at.desc(), StartPackTemplate.id.desc()).all()


def get_start_pack_template_or_error(template_id: int) -> StartPackTemplate:
    row = db.session.get(StartPackTemplate, int(template_id))
    if row is None:
        raise ValueError(f"Start pack template {template_id} not found")
    return row


def _resolve_object_type_id(payload: Dict[str, Any], idx: int) -> int:
    type_id = payload.get("object_type_id")
    if type_id is not None:
        row = db.session.get(ObjectType, int(type_id))
        if row is None:
            raise ValueError(f"items[{idx}] содержит неизвестный object_type_id")
        if not row.is_active:
            raise ValueError(f"items[{idx}] использует неактивный object type {row.code}")
        return int(row.id)

    code = payload.get("object_type_code")
    if not code:
        raise ValueError(f"items[{idx}] требует object_type_id или object_type_code")

    row = db.session.query(ObjectType).filter_by(code=str(code)).one_or_none()
    if row is None:
        raise ValueError(f"items[{idx}] содержит неизвестный object_type_code={code}")
    if not row.is_active:
        raise ValueError(f"items[{idx}] использует неактивный object type {row.code}")
    return int(row.id)


def normalize_template_items(items_payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(items_payload, list):
        raise ValueError("items должен быть массивом")

    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(items_payload, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{idx}] должен быть объектом")
        type_id = _resolve_object_type_id(raw, idx)
        quantity_raw = raw.get("quantity", 1)
        if quantity_raw is None:
            quantity_raw = 1
        quantity = int(quantity_raw)
        if quantity < 1:
            raise ValueError(f"items[{idx}].quantity должен быть >= 1")
        parameters = raw.get("parameters", raw.get("parameters_json", {}))
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise ValueError(f"items[{idx}].parameters должен быть объектом")

        normalized.append(
            {
                "row_key": str(raw.get("row_key") or idx),
                "parent_key": str(raw.get("parent_key") or "").strip() or None,
                "object_type_id": type_id,
                "quantity": quantity,
                "custom_name": str(raw.get("custom_name", "")),
                "district": str(raw.get("district", "core")),
                "parameters_json": dict(parameters),
                "sort_order": int(raw.get("sort_order", idx * 10) or idx * 10),
                "is_active": bool(raw.get("is_active", True)),
            }
        )

    if not normalized:
        raise ValueError("Шаблон стартового пакета не может быть пустым")
    return normalized


def create_start_pack_template(
    *,
    code: str,
    name: str,
    description: str,
    is_active: bool,
    is_builtin: bool,
    items_payload: Any,
) -> StartPackTemplate:
    code_val = str(code or "").strip()
    name_val = str(name or "").strip()
    if not code_val or not name_val:
        raise ValueError("code и name обязательны")
    if db.session.query(StartPackTemplate).filter_by(code=code_val).first() is not None:
        raise ValueError(f"Шаблон с code={code_val} уже существует")

    row = StartPackTemplate(
        code=code_val,
        name=name_val,
        description=str(description or ""),
        is_active=bool(is_active),
        is_builtin=bool(is_builtin),
    )
    db.session.add(row)
    db.session.flush()
    replace_start_pack_template_items(row=row, items_payload=items_payload)
    db.session.add(row)
    db.session.commit()
    return row


def update_start_pack_template(
    *,
    row: StartPackTemplate,
    payload: Dict[str, Any],
) -> StartPackTemplate:
    if "code" in payload:
        code_val = str(payload["code"] or "").strip()
        if not code_val:
            raise ValueError("code не может быть пустым")
        existing = db.session.query(StartPackTemplate).filter_by(code=code_val).first()
        if existing is not None and existing.id != row.id:
            raise ValueError(f"Шаблон с code={code_val} уже существует")
        row.code = code_val
    if "name" in payload:
        name_val = str(payload["name"] or "").strip()
        if not name_val:
            raise ValueError("name не может быть пустым")
        row.name = name_val
    if "description" in payload:
        row.description = str(payload["description"] or "")
    if "is_active" in payload:
        row.is_active = bool(payload["is_active"])
    if "is_builtin" in payload:
        row.is_builtin = bool(payload["is_builtin"])
    if "items" in payload:
        replace_start_pack_template_items(row=row, items_payload=payload.get("items") or [])

    db.session.add(row)
    db.session.commit()
    return row


def replace_start_pack_template_items(*, row: StartPackTemplate, items_payload: Any) -> None:
    normalized = normalize_template_items(items_payload)
    row.items.clear()
    db.session.flush()

    key_to_item: Dict[str, StartPackTemplateItem] = {}
    ordered: List[Tuple[Dict[str, Any], StartPackTemplateItem]] = []
    for idx, item in enumerate(normalized, start=1):
        line = StartPackTemplateItem(
            template_id=row.id,
            object_type_id=int(item["object_type_id"]),
            quantity=int(item.get("quantity", 1)),
            custom_name=str(item.get("custom_name", "")),
            district=str(item.get("district", "core")),
            parameters_json=dict(item.get("parameters_json", {}) or {}),
            parent_item_id=None,
            sort_order=int(item.get("sort_order", idx * 10) or idx * 10),
            is_active=bool(item.get("is_active", True)),
        )
        db.session.add(line)
        db.session.flush()
        row_key = str(item.get("row_key") or idx)
        key_to_item[row_key] = line
        ordered.append((item, line))

    for item, line in ordered:
        parent_key = item.get("parent_key")
        if not parent_key:
            continue
        parent = key_to_item.get(str(parent_key))
        if parent is None:
            raise ValueError(f"Не найден parent_key={parent_key}")
        line.parent_item_id = parent.id
        db.session.add(line)


def deactivate_start_pack_template(row: StartPackTemplate) -> StartPackTemplate:
    row.is_active = False
    db.session.add(row)
    db.session.commit()
    return row


def _resolve_template_for_session(
    session: GameSession,
    explicit_template_id: int | None,
) -> StartPackTemplate:
    if explicit_template_id is not None:
        template = db.session.get(StartPackTemplate, int(explicit_template_id))
    elif session.ruleset and session.ruleset.active_start_pack_template_id:
        template = db.session.get(
            StartPackTemplate,
            int(session.ruleset.active_start_pack_template_id),
        )
    else:
        template = db.session.query(StartPackTemplate).filter_by(is_active=True).first()

    if template is None:
        raise ValueError("Нет активного шаблона стартового пакета")
    if not template.is_active:
        raise ValueError("Шаблон стартового пакета неактивен")
    return template


def apply_start_pack_template_to_session(
    *,
    session: GameSession,
    template_id: int | None = None,
    commit: bool = True,
) -> List[ObjectInstance]:
    existing = (
        db.session.query(ObjectInstance)
        .filter_by(session_id=session.id, is_from_start_pack=True)
        .count()
    )
    if existing > 0:
        raise ValueError("Стартовый пакет уже добавлен")

    template = _resolve_template_for_session(session, template_id)
    items = [
        item
        for item in sorted(template.items, key=lambda it: (int(it.sort_order), int(it.id)))
        if item.is_active
    ]
    if not items:
        raise ValueError("В шаблоне стартового пакета нет активных строк")

    created_by_item_id: Dict[int, List[ObjectInstance]] = {}
    created: List[ObjectInstance] = []

    for item in items:
        parent_instances = created_by_item_id.get(int(item.parent_item_id or 0), [])
        quantity = max(1, int(item.quantity or 1))
        instances_for_item: List[ObjectInstance] = []

        for idx in range(quantity):
            parent_instance_id = None
            if parent_instances:
                parent_row = parent_instances[min(idx, len(parent_instances) - 1)]
                parent_instance_id = parent_row.id

            custom_name = (item.custom_name or "").strip()
            if not custom_name and item.object_type is not None:
                custom_name = item.object_type.name

            obj = ObjectInstance(
                session_id=session.id,
                object_type_id=item.object_type_id,
                custom_name=custom_name,
                current_parameters_json=dict(item.parameters_json or {}),
                is_from_start_pack=True,
                parent_instance_id=parent_instance_id,
                district=str(item.district or "core"),
                is_active=True,
            )
            db.session.add(obj)
            db.session.flush()
            instances_for_item.append(obj)
            created.append(obj)

        created_by_item_id[item.id] = instances_for_item

    if commit:
        db.session.commit()
    return created
