from __future__ import annotations

from typing import Any, Dict, List

from ..web.extensions import db
from ..web.models import Lot
from ..web.services.lot_validation import validate_and_normalize_lot_items


def normalize_lot_items(items_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return validate_and_normalize_lot_items(items_payload)


def delete_lot(lot: Lot) -> Dict[str, Any]:
    summary = {
        "lot_id": int(lot.id),
        "name": lot.name,
        "items_count": sum(max(1, int(item.quantity or 1)) for item in lot.items),
        "object_types_count": len(lot.items),
        "evaluations_count": len(lot.evaluations),
        "generated_objects_count": len(lot.generated_objects),
    }
    for obj in list(lot.generated_objects):
        obj.source_lot_id = None
        db.session.add(obj)
    db.session.delete(lot)
    return summary
