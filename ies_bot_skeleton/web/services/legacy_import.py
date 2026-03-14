from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..extensions import db
from ..models import GameSession, ObjectInstance, ObjectType
from .test_game_preset import LOT_KIND_TO_OBJECT_CODE, add_lot_payloads_to_session, load_lot_payloads_from_dir

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = ROOT / "resources" / "legacy_import" / "state.json"
DEFAULT_LOTS_DIR = ROOT / "resources" / "legacy_import" / "lots"


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
        code = LOT_KIND_TO_OBJECT_CODE.get(kind)
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

    lot_report = add_lot_payloads_to_session(
        session=session,
        payloads=load_lot_payloads_from_dir(lots_dir),
        type_map=type_map,
    )
    report["lots_created"] += int(lot_report.get("lots_created", 0) or 0)
    report["lot_items_created"] += int(lot_report.get("lot_items_created", 0) or 0)
    report["skipped"].extend(list(lot_report.get("skipped") or []))

    db.session.commit()
    return report
