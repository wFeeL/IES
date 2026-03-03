# controllers_wear.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

from .adaptive import GameConst
from .adapters import OrdersAdapter
from .utils import as_float, safe_getattr

@dataclass
class LineOff:
    sub_id: str
    line_no: int
    reason: str

def wear_triggers(psm: Any, gc: GameConst) -> List[LineOff]:
    actions: List[LineOff] = []
    nets = safe_getattr(psm, "networks", {}) or {}
    if not isinstance(nets, dict):
        return actions

    for _, net in nets.items():
        wear = as_float(safe_getattr(net, "wear", 0.0), 0.0)
        up = as_float(safe_getattr(net, "upflow", 0.0), 0.0)
        dn = as_float(safe_getattr(net, "downflow", 0.0), 0.0)
        online = bool(safe_getattr(net, "online", True))
        flow = max(abs(up), abs(dn))
        if wear <= 0 or not online:
            continue

        # Prefer preventive maintenance only when impact is low.
        low_flow = flow <= 10.0
        near_fail = wear >= max(0.0, gc.wear_limit - 0.5)
        if not low_flow and not near_fail:
            continue
        if wear < gc.wear_preempt:
            continue

        loc = safe_getattr(net, "location", None)
        if isinstance(loc, (list, tuple)):
            for item in loc:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    sub_id, line_no = item[0], item[1]
                    actions.append(LineOff(str(sub_id), int(line_no), f"wear={wear:.1f}, flow={flow:.1f}"))
                    break

    # Send at most one manual line-off per tick to avoid aggressive disconnect cascades.
    return actions[:1]

def apply_wear_actions(adapter: OrdersAdapter, actions: List[LineOff]) -> None:
    for a in actions:
        adapter.line_off(a.sub_id, a.line_no)
