from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from ..models import GameSession, Lot, LotItem, ObjectInstance
from ies_bot_skeleton.domain.lot_analysis.types import (
    Assumptions,
    Branch,
    Budget,
    Game,
    Lot as LotModel,
    NetworkPlan,
    ObjectItem,
    State,
)

DOMAIN_KIND_MAP = {
    "main": "main",
    "main_substation": "main",
    "main_substation_hq": "main",
    "mini": "miniA",
    "mini_substation": "miniA",
    "mini_substation_a": "miniA",
    "mini_substation_b": "miniB",
    "minia": "miniA",
    "minib": "miniB",
    "house": "houseA",
    "housea": "houseA",
    "houseb": "houseB",
    "office": "office",
    "factory": "factory",
    "wind": "wind",
    "solar": "solarRobot",
    "solar_robot": "solarRobot",
    "solarrobot": "solarRobot",
    "ses": "solarRobot",
    "cyber_solar": "solarRobot",
    "tps": "tps",
    "tes": "tps",
    "storage": "storage",
    "accumulator": "storage",
    "battery": "storage",
}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or ch == "_")


def _to_domain_kind(code: str) -> Optional[str]:
    return DOMAIN_KIND_MAP.get(_norm(code))


def _merged_params(instance: ObjectInstance) -> Dict:
    return instance.merged_parameters() if hasattr(instance, "merged_parameters") else {}


def _object_item_id(prefix: str, source_id: int, fallback: str = "") -> str:
    return f"{prefix}{source_id}" if source_id else (fallback or prefix)


def instance_to_object_item(instance: ObjectInstance) -> Optional[ObjectItem]:
    if not instance.object_type:
        return None
    kind = _to_domain_kind(instance.object_type.code)
    if kind is None:
        return None

    params = _merged_params(instance)
    qty = int(params.get("qty", 1) or 1)
    if qty < 1:
        qty = 1

    object_id = str(
        params.get("object_id") or _object_item_id("OBJ", instance.id, fallback=str(instance.id))
    )

    connection_point = (
        params.get("connection_point")
        or params.get("point")
        or params.get("cell")
        or params.get("slot")
    )
    meta = {
        "source": "session",
        "instance_id": instance.id,
        "district": instance.district,
        "object_type_code": instance.object_type.code,
    }
    if connection_point:
        meta["connection_point"] = str(connection_point).strip().upper()

    return ObjectItem(
        kind=kind,
        id=object_id,
        qty=qty,
        contract_rub_per_tick=float(params.get("contract_rub_per_tick", 0.0) or 0.0),
        tariff_rub_per_mw_tick=float(params.get("tariff_rub_per_mw_tick", 0.0) or 0.0),
        meta=meta,
    )


def lot_item_to_object_item(lot_item: LotItem) -> Optional[ObjectItem]:
    if not lot_item.object_type:
        return None
    kind = _to_domain_kind(lot_item.object_type.code)
    if kind is None:
        return None

    params = dict(lot_item.object_type.default_parameters_json or {})
    params.update(dict(lot_item.overrides_json or {}))
    object_id = str(
        params.get("object_id")
        or f"LOT{lot_item.lot_id}_IT{lot_item.id}_{lot_item.object_type.code}"
    )
    connection_point = (
        params.get("connection_point")
        or params.get("point")
        or params.get("cell")
        or params.get("slot")
    )
    meta = {
        "source": "lot",
        "lot_item_id": lot_item.id,
        "object_type_code": lot_item.object_type.code,
    }
    if connection_point:
        meta["connection_point"] = str(connection_point).strip().upper()

    return ObjectItem(
        kind=kind,
        id=object_id,
        qty=max(1, int(lot_item.quantity or 1)),
        contract_rub_per_tick=float(params.get("contract_rub_per_tick", 0.0) or 0.0),
        tariff_rub_per_mw_tick=float(params.get("tariff_rub_per_mw_tick", 0.0) or 0.0),
        meta=meta,
    )


def lot_to_domain_lot(lot: Lot) -> LotModel:
    items: List[ObjectItem] = []
    for it in lot.items:
        mapped = lot_item_to_object_item(it)
        if mapped is not None:
            items.append(mapped)

    return LotModel(
        lot_id=f"LOT{lot.id}",
        title=lot.name,
        note=lot.note,
        items=items,
        suggested_bid=lot.current_bid,
    )


def collect_owned_items(session: GameSession) -> List[ObjectItem]:
    out: List[ObjectItem] = []
    for obj in session.objects:
        if not obj.is_active:
            continue
        mapped = instance_to_object_item(obj)
        if mapped is not None:
            out.append(mapped)
    return out


def _build_network_plan(objects: Iterable[ObjectInstance], cfg: Dict) -> NetworkPlan:
    gen_codes = {"generator", "storage"}
    load_codes = {"consumer"}
    by_district: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"gen": [], "load": []})

    soft_default = float((cfg.get("network", {}) or {}).get("soft_flow_mw", 30.0))

    for obj in objects:
        if not obj.is_active:
            continue
        item = instance_to_object_item(obj)
        if item is None:
            continue
        district = str(obj.district or "default")
        category = _norm(obj.object_type.category if obj.object_type else "")
        if category in gen_codes:
            by_district[district]["gen"].append(item.id)
        elif category in load_codes:
            by_district[district]["load"].append(item.id)

    branches: List[Branch] = []
    for district, groups in by_district.items():
        if groups["gen"]:
            branches.append(
                Branch(
                    name=f"{district.upper()}_GEN",
                    role="gen",
                    objects=groups["gen"],
                    soft_flow_limit_mw=soft_default,
                )
            )
        if groups["load"]:
            branches.append(
                Branch(
                    name=f"{district.upper()}_LOAD",
                    role="load",
                    objects=groups["load"],
                    soft_flow_limit_mw=soft_default,
                )
            )

    if not branches:
        branches = [
            Branch(name="GEN", role="gen", objects=[], soft_flow_limit_mw=soft_default),
            Branch(name="LOAD", role="load", objects=[], soft_flow_limit_mw=soft_default),
        ]

    return NetworkPlan(mode="branches", branches=branches)


def session_to_state(
    session: GameSession,
    cfg: Dict,
) -> Tuple[State, List[ObjectItem]]:
    owned_items = collect_owned_items(session)
    time_cfg = cfg.get("time", {}) or {}
    eval_cfg = cfg.get("evaluation", {}) or {}
    scenarios_cfg = cfg.get("scenarios", {}) or {}
    corridor = dict(
        scenarios_cfg.get("corridor")
        or {
            "wind_mul": 0.10,
            "solar_mul": 0.10,
            "load_mul": 0.10,
        }
    )
    allpay_spent = max(0.0, float(getattr(session, "allpay_spent", 0.0) or 0.0))
    owned_lot_ids = [
        f"LOT{int(lot.id)}"
        for lot in session.lots
        if str(lot.status or "") == "bought"
    ]

    state = State(
        schema_version=1,
        game=Game(
            ticks_per_day=int(time_cfg.get("ticks_per_day", 48) or 48),
            horizon_ticks=int(time_cfg.get("horizon_ticks", 48) or 48),
        ),
        # Legacy domain model separates cash and all-pay spend.
        # Purchase spend is represented by owned lots/objects, not by allpay_spent.
        budget=Budget(cash=float(session.budget_total or 0.0), allpay_spent=float(allpay_spent)),
        owned_lots=owned_lot_ids,
        owned_objects_override=list(owned_items),
        network_plan=_build_network_plan(session.objects, cfg),
        assumptions=Assumptions(
            pwin_default=float((cfg.get("auction", {}) or {}).get("pwin_default", 0.35)),
            risk_mode=str(eval_cfg.get("risk_mode", "conservative")),
            corridor={
                "wind_mul": float(corridor.get("wind_mul", 0.10)),
                "solar_mul": float(corridor.get("solar_mul", 0.10)),
                "load_mul": float(corridor.get("load_mul", 0.10)),
            },
            storage_soc_init_fraction=float(eval_cfg.get("storage_soc_init_fraction", 0.5)),
        ),
    )
    return state, owned_items
