from __future__ import annotations

from typing import Any

from ..models import ObjectInstance

PENDING_INTEGRATION = "pending_connection"
INTEGRATED = "integrated"
ORIGIN_BOUGHT_LOT = "bought_lot"

_STATE_KEY = "integration_state"
_ORIGIN_KEY = "object_origin"


def _params(obj: ObjectInstance) -> dict[str, Any]:
    return dict(obj.current_parameters_json or {})


def integration_state(obj: ObjectInstance) -> str:
    if obj.source_lot_id is None:
        return INTEGRATED
    if not bool(obj.is_active):
        return INTEGRATED
    params = _params(obj)
    connection_inputs = list(params.get("connection_inputs") or [])
    if connection_inputs:
        for row in connection_inputs:
            if not isinstance(row, dict):
                return PENDING_INTEGRATION
            if bool(row.get("required", True)) and row.get("parent_instance_id") in (None, 0, "0", ""):
                return PENDING_INTEGRATION
        return INTEGRATED
    if params.get("secondary_parent_instance_id") not in (None, 0, "0", ""):
        if obj.parent_instance_id in (None, 0, "0", ""):
            return PENDING_INTEGRATION
        return INTEGRATED
    if obj.parent_instance_id in (None, 0, "0", ""):
        return PENDING_INTEGRATION
    return INTEGRATED


def refresh_integration_state(obj: ObjectInstance) -> ObjectInstance:
    params = _params(obj)
    state = integration_state(obj)
    if obj.source_lot_id is not None:
        params[_ORIGIN_KEY] = ORIGIN_BOUGHT_LOT
        params[_STATE_KEY] = state
    else:
        params.pop(_ORIGIN_KEY, None)
        params.pop(_STATE_KEY, None)
    obj.current_parameters_json = params
    return obj


def mark_generated_from_lot(obj: ObjectInstance, *, lot_id: int) -> ObjectInstance:
    obj.source_lot_id = int(lot_id)
    return refresh_integration_state(obj)


def is_pending_integration(obj: ObjectInstance) -> bool:
    return integration_state(obj) == PENDING_INTEGRATION


__all__ = [
    "INTEGRATED",
    "ORIGIN_BOUGHT_LOT",
    "PENDING_INTEGRATION",
    "integration_state",
    "is_pending_integration",
    "mark_generated_from_lot",
    "refresh_integration_state",
]
