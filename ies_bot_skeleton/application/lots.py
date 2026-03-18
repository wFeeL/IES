from __future__ import annotations

from typing import Any, Dict, List

from ..web.extensions import db
from ..web.models import Lot
from ..web.services.lot_validation import validate_and_normalize_lot_items


def normalize_lot_items(items_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return validate_and_normalize_lot_items(items_payload)


def delete_lot(lot: Lot) -> Dict[str, Any]:
    status = str(lot.status or "")
    if status == "bought":
        raise ValueError(
            "Нельзя удалить купленный лот: сначала отмените покупку, чтобы безопасно убрать связанные объекты."
        )
    if lot.generated_objects:
        raise ValueError(
            "Нельзя удалить лот, пока у него есть связанные объекты в энергосистеме."
        )

    summary = {
        "lot_id": int(lot.id),
        "name": lot.name,
        "items_count": sum(max(1, int(item.quantity or 1)) for item in lot.items),
        "object_types_count": len(lot.items),
        "evaluations_count": len(lot.evaluations),
        "generated_objects_count": len(lot.generated_objects),
    }
    db.session.delete(lot)
    return summary
