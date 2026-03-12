from __future__ import annotations

from typing import Any, Dict, List

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from ...application.lots import normalize_lot_items
from ..extensions import db
from ..models import GameSession, Lot, LotItem


class ApiError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def json_payload() -> Dict[str, Any]:
    raw_body = request.get_data(cache=True)
    if not raw_body or not raw_body.strip():
        return {}

    if not request.is_json:
        return {}

    try:
        payload = request.get_json(silent=False)
    except BadRequest as exc:
        raise ApiError(
            code="bad_request",
            message="Некорректный JSON",
            status_code=400,
        ) from exc

    if not isinstance(payload, dict):
        raise ApiError(
            code="bad_request",
            message="JSON payload должен быть объектом",
            status_code=400,
        )

    return payload


def get_session_or_404(session_id: int) -> GameSession:
    row = db.session.get(GameSession, session_id)
    if row is None:
        raise ApiError(code="not_found", message=f"Сессия {session_id} не найдена", status_code=404)
    return row


def get_lot_or_404(lot_id: int) -> Lot:
    row = db.session.get(Lot, lot_id)
    if row is None:
        raise ApiError(code="not_found", message=f"Лот {lot_id} не найден", status_code=404)
    return row


def lot_items_from_payload(lot: Lot, items_payload: List[Dict[str, Any]]) -> None:
    normalized = normalize_lot_items(items_payload)
    lot.items.clear()
    for row in normalized:
        lot.items.append(
            LotItem(
                object_type_id=int(row["object_type_id"]),
                quantity=max(1, int(row.get("quantity", 1) or 1)),
                overrides_json=dict(row.get("overrides", {}) or {}),
            )
        )


def api_error_response(exc: ApiError):
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        ),
        exc.status_code,
    )


def value_error_response(exc: ValueError):
    if isinstance(exc, ApiError):
        return api_error_response(exc)
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": "validation_error",
                    "message": str(exc),
                    "details": {},
                },
            }
        ),
        400,
    )
