from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ..extensions import db
from ..models import GameSession, Lot, LotItem, ObjectType, Ruleset
from .start_pack import apply_start_pack_template_to_session

ROOT = Path(__file__).resolve().parents[2]

TEST_GAME_RULESET_CODE = "ies_test_game_2026"
TEST_GAME_RULESET_VERSION = "1"
TEST_GAME_RULESET_NAME = "Тестовая игра ИЭС"

TEST_GAME_START_PACK_CODE = "test_game_default"
TEST_GAME_START_PACK_NAME = "Стартовый пакет тестовой игры"
TEST_GAME_START_PACK_DESCRIPTION = (
    "Главная подстанция + мини-подстанция + солнечная панель + жилой дом."
)

TEST_GAME_DEFAULT_SESSION_TITLE = "Тестовая игра"
TEST_GAME_BUNDLED_FORECAST_NAME = "Прогноз тестовой игры"
TEST_GAME_FORECAST_SOURCE_LABEL = "Встроенный прогноз тестовой игры"
TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME = "Прогноз игры"

TEST_GAME_OBJECT_CODES = (
    "main_substation",
    "mini_substation_a",
    "cyber_solar",
    "house",
    "office",
    "factory",
    "wind",
    "tps",
    "storage",
)

TEST_GAME_LOTS_DIR = ROOT / "resources" / "legacy_import" / "lots"
TEST_GAME_LOT_FILES = ("L01.json", "L02.json", "L03.json", "L04.json", "L05.json")
TEST_GAME_LOT_PATHS = tuple(TEST_GAME_LOTS_DIR / name for name in TEST_GAME_LOT_FILES)

LOT_KIND_TO_OBJECT_CODE = {
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


def is_test_game_ruleset(ruleset: Ruleset | None) -> bool:
    if ruleset is None:
        return False
    return (
        str(ruleset.code) == TEST_GAME_RULESET_CODE
        and str(ruleset.version or "") == TEST_GAME_RULESET_VERSION
    )


def preferred_default_ruleset() -> Ruleset | None:
    preferred = (
        db.session.query(Ruleset)
        .filter_by(
            code=TEST_GAME_RULESET_CODE,
            version=TEST_GAME_RULESET_VERSION,
            is_active=True,
        )
        .one_or_none()
    )
    if preferred is not None:
        return preferred
    return (
        db.session.query(Ruleset)
        .filter_by(is_active=True)
        .order_by(Ruleset.created_at.desc(), Ruleset.id.desc())
        .first()
    )


def load_lot_payloads_from_paths(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        payload = _read_json(path)
        if payload:
            out.append(payload)
    return out


def load_lot_payloads_from_dir(lots_dir: Path) -> List[Dict[str, Any]]:
    return load_lot_payloads_from_paths(sorted(lots_dir.glob("*.json")))


def load_test_game_lot_payloads() -> List[Dict[str, Any]]:
    return load_lot_payloads_from_paths(TEST_GAME_LOT_PATHS)


def _object_type_map() -> Dict[str, ObjectType]:
    rows = db.session.query(ObjectType).all()
    return {row.code: row for row in rows}


def add_lot_payloads_to_session(
    *,
    session: GameSession,
    payloads: Iterable[Dict[str, Any]],
    type_map: Dict[str, ObjectType] | None = None,
) -> Dict[str, Any]:
    resolved_types = type_map or _object_type_map()
    report: Dict[str, Any] = {
        "session_id": int(session.id),
        "lots_created": 0,
        "lot_items_created": 0,
        "skipped": [],
    }

    for payload in payloads:
        lot_name = str(payload.get("title") or payload.get("lot_id") or "").strip()
        if not lot_name:
            report["skipped"].append("lot payload without title")
            continue

        if (
            db.session.query(Lot).filter_by(session_id=session.id, name=lot_name).first()
            is not None
        ):
            report["skipped"].append(f"lot {lot_name}: already exists")
            continue

        bid_value = float(payload.get("suggested_bid", 0.0) or 0.0)
        lot = Lot(
            session_id=session.id,
            name=lot_name,
            scope="normal",
            status="available",
            base_bid=bid_value,
            current_bid=bid_value,
            note=str(payload.get("note", "")),
            available_round=1,
        )
        db.session.add(lot)
        db.session.flush()
        report["lots_created"] += 1

        for idx, item in enumerate(payload.get("items", []) or [], start=1):
            kind = _norm(str(item.get("kind", item.get("type", ""))))
            code = LOT_KIND_TO_OBJECT_CODE.get(kind)
            if not code or code not in resolved_types:
                report["skipped"].append(f"lot {lot_name} item {idx}: unknown kind={kind}")
                continue

            quantity = max(1, int(item.get("qty", 1) or 1))
            overrides = {
                "contract_rub_per_tick": float(item.get("contract_rub_per_tick", 0.0) or 0.0),
                "tariff_rub_per_mw_tick": float(item.get("tariff_rub_per_mw_tick", 0.0) or 0.0),
                "legacy_id": str(item.get("id", "")),
                "legacy_kind": str(item.get("kind", item.get("type", ""))),
            }

            lot_item = LotItem(
                lot_id=lot.id,
                object_type_id=resolved_types[code].id,
                quantity=quantity,
                overrides_json=overrides,
            )
            db.session.add(lot_item)
            report["lot_items_created"] += 1

    return report


def bootstrap_test_game_session(
    session: GameSession,
    *,
    commit: bool = True,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "session_id": int(session.id),
        "objects_created": 0,
        "lots_created": 0,
        "lot_items_created": 0,
        "skipped": [],
    }
    if not is_test_game_ruleset(session.ruleset):
        return report
    if session.objects or session.lots:
        report["skipped"].append("session already contains objects or lots")
        return report

    created = apply_start_pack_template_to_session(session=session, commit=False)
    report["objects_created"] = len(created)

    payloads = load_test_game_lot_payloads()
    lot_report = add_lot_payloads_to_session(
        session=session,
        payloads=payloads,
    )
    report["lots_created"] = int(lot_report.get("lots_created", 0) or 0)
    report["lot_items_created"] = int(lot_report.get("lot_items_created", 0) or 0)
    skipped = list(lot_report.get("skipped") or [])
    if skipped:
        raise ValueError(
            "Не удалось полностью инициализировать тестовую игру: " + "; ".join(skipped)
        )
    expected_lots = len(payloads)
    if report["lots_created"] != expected_lots:
        raise ValueError(
            "Не удалось полностью инициализировать тестовую игру: "
            f"ожидалось {expected_lots} лотов, создано {report['lots_created']}"
        )
    if commit:
        db.session.commit()
    return report
