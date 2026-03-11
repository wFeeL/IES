from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..extensions import db
from ..models import GameSession, Lot, LotItem, ObjectInstance, ObjectType

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = ROOT / "lot_tool" / "data" / "state.json"
DEFAULT_LOTS_DIR = ROOT / "lot_tool" / "data" / "lots"

KIND_TO_OBJECT_CODE = {
    "main": "main_substation",
    "minia": "mini_substation_a",
    "minib": "mini_substation_b",
    "housea": "house",
    "houseb": "house",
    "office": "office",
    "factory": "factory",
    "wind": "wind",
    "solarrobot": "cyber_solar",
    "tps": "tps",
    "storage": "storage",
}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or ch == "_")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _type_by_code() -> Dict[str, ObjectType]:
    rows = db.session.query(ObjectType).all()
    return {row.code: row for row in rows}


def import_legacy_data(
    *,
    session_id: int,
    state_path: Path = DEFAULT_STATE_PATH,
    lots_dir: Path = DEFAULT_LOTS_DIR,
) -> Dict[str, Any]:
    session = db.session.get(GameSession, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    report: Dict[str, Any] = {
        "session_id": session_id,
        "objects_created": 0,
        "lots_created": 0,
        "lot_items_created": 0,
        "skipped": [],
    }

    type_map = _type_by_code()

    state_data = _read_json(state_path)
    for idx, row in enumerate(state_data.get("owned_objects_override", []) or [], start=1):
        kind = _norm(str(row.get("kind", "")))
        code = KIND_TO_OBJECT_CODE.get(kind)
        if not code or code not in type_map:
            report["skipped"].append(f"state.owned_objects_override[{idx}] kind={kind}: unknown")
            continue

        qty = max(1, int(row.get("qty", 1) or 1))
        for copy_idx in range(qty):
            meta = dict(row.get("meta", {}) or {})
            connection_point = (
                meta.get("connection_point")
                or meta.get("point")
                or meta.get("cell")
                or meta.get("slot")
            )
            params = {
                "contract_rub_per_tick": float(row.get("contract_rub_per_tick", 0.0) or 0.0),
                "tariff_rub_per_mw_tick": float(row.get("tariff_rub_per_mw_tick", 0.0) or 0.0),
                "object_id": f"LEGACY_{row.get('id', 'X')}_{copy_idx+1}",
            }
            if connection_point:
                params["connection_point"] = str(connection_point).strip().upper()
            obj = ObjectInstance(
                session_id=session.id,
                object_type_id=type_map[code].id,
                custom_name=str(row.get("id", "legacy")),
                current_parameters_json=params,
                source_lot_id=None,
                is_from_start_pack=False,
                parent_instance_id=None,
                district="legacy",
                is_active=True,
            )
            db.session.add(obj)
            report["objects_created"] += 1

    for lot_path in sorted(lots_dir.glob("*.json")):
        payload = _read_json(lot_path)
        lot_name = str(payload.get("title") or payload.get("lot_id") or lot_path.stem)
        if (
            db.session.query(Lot).filter_by(session_id=session.id, name=lot_name).first()
            is not None
        ):
            report["skipped"].append(f"lot {lot_name}: already exists")
            continue

        lot = Lot(
            session_id=session.id,
            name=lot_name,
            scope="normal",
            status="available",
            base_bid=float(payload.get("suggested_bid", 0.0) or 0.0),
            current_bid=float(payload.get("suggested_bid", 0.0) or 0.0),
            note=str(payload.get("note", "")),
            available_round=1,
        )
        db.session.add(lot)
        db.session.flush()
        report["lots_created"] += 1

        for idx, item in enumerate(payload.get("items", []) or [], start=1):
            kind = _norm(str(item.get("kind", item.get("type", ""))))
            code = KIND_TO_OBJECT_CODE.get(kind)
            if not code or code not in type_map:
                report["skipped"].append(f"lot {lot_name} item {idx}: unknown kind={kind}")
                continue
            quantity = max(1, int(item.get("qty", 1) or 1))
            meta = dict(item.get("meta", {}) or {})
            connection_point = (
                meta.get("connection_point")
                or meta.get("point")
                or meta.get("cell")
                or meta.get("slot")
            )
            overrides = {
                "contract_rub_per_tick": float(item.get("contract_rub_per_tick", 0.0) or 0.0),
                "tariff_rub_per_mw_tick": float(item.get("tariff_rub_per_mw_tick", 0.0) or 0.0),
                "legacy_id": str(item.get("id", "")),
                "legacy_kind": str(item.get("kind", item.get("type", ""))),
            }
            if connection_point:
                overrides["connection_point"] = str(connection_point).strip().upper()
            lot_item = LotItem(
                lot_id=lot.id,
                object_type_id=type_map[code].id,
                quantity=quantity,
                overrides_json=overrides,
            )
            db.session.add(lot_item)
            report["lot_items_created"] += 1

    db.session.commit()
    return report
