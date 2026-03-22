from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Set

from ..models import GameSession, ObjectInstance
from .network import MAIN_CODES
from .purchased_objects import integration_state, is_pending_integration


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or ch == "_")


def _active_objects(session: GameSession) -> List[ObjectInstance]:
    return [obj for obj in session.objects if bool(obj.is_active)]


def _main_object_ids(objects: Sequence[ObjectInstance]) -> List[int]:
    out: List[int] = []
    for obj in objects:
        object_type = obj.object_type
        if object_type is None:
            continue
        if _norm(object_type.code) in MAIN_CODES:
            out.append(int(obj.id))
    return out


def _children_map(objects: Sequence[ObjectInstance]) -> Mapping[int, List[int]]:
    children: Dict[int, List[int]] = {int(obj.id): [] for obj in objects}
    for obj in objects:
        if not obj.parent_instance_id:
            continue
        parent_id = int(obj.parent_instance_id)
        if parent_id in children:
            children[parent_id].append(int(obj.id))
    return children


def _reachable_ids(objects: Sequence[ObjectInstance], *, root_id: int) -> Set[int]:
    children = _children_map(objects)
    reachable: Set[int] = set()
    stack = [int(root_id)]
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(children.get(node, []))
    return reachable


def _object_label(obj: ObjectInstance) -> str:
    if str(obj.custom_name or "").strip():
        return str(obj.custom_name)
    if obj.object_type is not None and str(obj.object_type.name or "").strip():
        return str(obj.object_type.name)
    return f"Объект #{int(obj.id)}"


def _bought_lot_name(session: GameSession, lot_id: int) -> str:
    for lot in session.lots:
        if int(lot.id) != int(lot_id):
            continue
        return str(lot.name or f"Лот #{lot_id}")
    return f"Лот #{lot_id}"


def network_readiness_summary(session: GameSession) -> Dict[str, Any]:
    objects = _active_objects(session)
    bought_lot_ids = {int(lot.id) for lot in session.lots if str(lot.status or "") == "bought"}
    purchased_objects = [
        obj
        for obj in objects
        if obj.source_lot_id is not None and int(obj.source_lot_id) in bought_lot_ids
    ]
    if not purchased_objects:
        return {
            "has_purchased_objects": False,
            "has_unconnected_purchased_objects": False,
            "action_required": False,
            "bought_lots_count": len(bought_lot_ids),
            "purchased_objects_count": 0,
            "unconnected_purchased_objects_count": 0,
            "unconnected_lots_count": 0,
            "lots": [],
            "message": "",
        }

    main_ids = _main_object_ids(objects)
    reachable: Set[int] = set()
    if main_ids:
        reachable = _reachable_ids(objects, root_id=int(main_ids[0]))

    unconnected = [
        obj
        for obj in purchased_objects
        if bool(is_pending_integration(obj)) or int(obj.id) not in reachable
    ]
    grouped: Dict[int, List[ObjectInstance]] = defaultdict(list)
    for obj in unconnected:
        if obj.source_lot_id is None:
            continue
        grouped[int(obj.source_lot_id)].append(obj)

    lots_payload = []
    for lot_id in sorted(grouped):
        rows = grouped[lot_id]
        lots_payload.append(
            {
                "lot_id": int(lot_id),
                "lot_name": _bought_lot_name(session, lot_id),
                "objects_count": len(rows),
                "object_ids": [int(obj.id) for obj in rows],
                "objects": [
                    {
                        "object_id": int(obj.id),
                        "label": _object_label(obj),
                        "integration_state": integration_state(obj),
                    }
                    for obj in rows
                ],
            }
        )

    has_unconnected = bool(unconnected)
    if has_unconnected:
        if not main_ids:
            message = (
                "После покупки лотов добавленные объекты не подключены: в энергосистеме отсутствует "
                "главная подстанция. Подключите объекты в разделе энергосистемы, иначе оценка "
                "полезности и ставок остальных лотов может быть занижена или некорректна."
            )
        else:
            message = (
                "После покупки лотов добавленные объекты нужно подключить в разделе энергосистемы, "
                "иначе оценка полезности и ставок остальных лотов может быть занижена или некорректна."
            )
    else:
        message = ""

    return {
        "has_purchased_objects": True,
        "has_unconnected_purchased_objects": has_unconnected,
        "action_required": has_unconnected,
        "bought_lots_count": len(bought_lot_ids),
        "purchased_objects_count": len(purchased_objects),
        "unconnected_purchased_objects_count": len(unconnected),
        "unconnected_lots_count": len(lots_payload),
        "lots": lots_payload,
        "message": message,
    }


__all__ = ["network_readiness_summary"]
