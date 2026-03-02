from __future__ import annotations

import json
from dataclasses import fields
from typing import Any, Dict, List, Type, TypeVar

from ..model.types import Assumptions, Branch, Budget, Game, Lot, NetworkPlan, ObjectItem, State

T = TypeVar("T")


def _dict_to_dataclass(cls: Type[T], d: Dict[str, Any]) -> T:
    kwargs = {}
    fset = {f.name for f in fields(cls)}
    for k, v in d.items():
        if k in fset:
            kwargs[k] = v
    return cls(**kwargs)  # type: ignore[arg-type]


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_lot(path: str) -> Lot:
    d = load_json(path)
    items = [_dict_to_dataclass(ObjectItem, it) for it in (d.get("items", []) or [])]
    return Lot(
        lot_id=str(d.get("lot_id", "")),
        title=str(d.get("title", "")),
        note=str(d.get("note", "")),
        items=items,
        suggested_bid=d.get("suggested_bid"),
    )


def load_state(path: str) -> State:
    d = load_json(path)

    game = _dict_to_dataclass(Game, d.get("game", {}) or {})
    budget = _dict_to_dataclass(Budget, d.get("budget", {}) or {})

    owned_override = [_dict_to_dataclass(ObjectItem, it) for it in (d.get("owned_objects_override", []) or [])]

    np = d.get("network_plan", {}) or {}
    branches = [_dict_to_dataclass(Branch, b) for b in (np.get("branches", []) or [])]
    net_plan = NetworkPlan(mode=str(np.get("mode", "branches")), branches=branches)

    assumptions = _dict_to_dataclass(Assumptions, d.get("assumptions", {}) or {})

    return State(
        game=game,
        budget=budget,
        owned_lots=list(d.get("owned_lots", []) or []),
        owned_objects_override=owned_override,
        network_plan=net_plan,
        assumptions=assumptions,
    )
