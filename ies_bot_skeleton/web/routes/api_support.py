from __future__ import annotations

from typing import Any, Dict, List

from flask import jsonify, request

from ..extensions import db
from ..models import GameSession, Lot, LotItem
from ..services.lot_validation import validate_and_normalize_lot_items


def json_payload() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def get_session_or_404(session_id: int) -> GameSession:
    row = db.session.get(GameSession, session_id)
    if row is None:
        raise ValueError(f"Session {session_id} not found")
    return row


def get_lot_or_404(lot_id: int) -> Lot:
    row = db.session.get(Lot, lot_id)
    if row is None:
        raise ValueError(f"Lot {lot_id} not found")
    return row


def lot_items_from_payload(lot: Lot, items_payload: List[Dict[str, Any]]) -> None:
    normalized = validate_and_normalize_lot_items(items_payload)
    lot.items.clear()
    for row in normalized:
        lot.items.append(
            LotItem(
                object_type_id=int(row["object_type_id"]),
                quantity=max(1, int(row.get("quantity", 1) or 1)),
                overrides_json=dict(row.get("overrides", {}) or {}),
            )
        )


def value_error_response(exc: ValueError):
    return jsonify({"ok": False, "error": str(exc)}), 400
