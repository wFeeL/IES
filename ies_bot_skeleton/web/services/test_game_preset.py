from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ..extensions import db
from ..models import GameSession, Lot, LotItem, ObjectType, Ruleset
from .start_pack import apply_start_pack_template_to_session

TEST_GAME_RULESET_CODE = "ies_test_game_2026"
TEST_GAME_RULESET_VERSION = "1"
TEST_GAME_RULESET_NAME = "Тестовая игра ИЭС 2026"

TEST_GAME_START_PACK_CODE = "test_game_default_2026"
TEST_GAME_START_PACK_NAME = "Стартовый пакет тестовой игры 2026"
TEST_GAME_START_PACK_DESCRIPTION = (
    "Главная подстанция, мини-подстанция нагрузки и базовый дом по правилам ИЭС 2026."
)

TEST_GAME_DEFAULT_SESSION_TITLE = "Тестовая игра 2026"
TEST_GAME_BUNDLED_FORECAST_NAME = "Прогноз тестовой игры 2026"
TEST_GAME_FORECAST_SOURCE_LABEL = "Встроенный прогноз ИЭС 2026"
TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME = "Прогноз 2026"

TEST_GAME_OBJECT_CODES = (
    "main_substation",
    "mini_substation",
    "solar",
    "wind",
    "storage",
    "house_a",
    "house_b",
    "office",
    "factory",
    "hospital",
)

TEST_GAME_GENERATED_LOTS_SEED = 20260323
LOT_KIND_TO_OBJECT_CODE: Dict[str, str] = {
    "main": "main_substation",
    "main_substation": "main_substation",
    "mini": "mini_substation",
    "minia": "mini_substation",
    "minib": "mini_substation",
    "mini_substation": "mini_substation",
    "mini_substation_a": "mini_substation",
    "mini_substation_b": "mini_substation",
    "solar": "solar",
    "solarrobot": "solar",
    "cyber_solar": "solar",
    "wind": "wind",
    # Legacy thermal plant is imported as a generic dispatchable generation proxy.
    "tps": "wind",
    "storage": "storage",
    "house": "house_a",
    "housea": "house_a",
    "house_b": "house_b",
    "houseb": "house_b",
    "office": "office",
    "factory": "factory",
    "hospital": "hospital",
}
LOT_PATTERNS: Sequence[Dict[str, Any]] = (
    {"title": "Жилой север", "items": (("house_a", 2), ("mini_substation", 1))},
    {"title": "Жилой юг", "items": (("house_b", 2),)},
    {"title": "Офисный кластер", "items": (("office", 2), ("mini_substation", 1))},
    {"title": "Промышленный узел", "items": (("factory", 1), ("mini_substation", 1))},
    {"title": "Критическая нагрузка", "items": (("hospital", 1), ("mini_substation", 1))},
    {"title": "Солнечный контур", "items": (("solar", 2),)},
    {"title": "Ветровой контур", "items": (("wind", 2),)},
    {"title": "Сетевой буфер", "items": (("storage", 1), ("mini_substation", 1))},
    {"title": "Гибкий микс", "items": (("solar", 1), ("storage", 1), ("mini_substation", 1))},
    {"title": "Потребительский резерв", "items": (("house_a", 1), ("office", 1), ("storage", 1))},
    {"title": "Пиковый ветер", "items": (("wind", 1), ("storage", 1))},
    {"title": "Солнечная больница", "items": (("hospital", 1), ("solar", 1), ("mini_substation", 1))},
)


def is_test_game_ruleset(ruleset: Ruleset | None) -> bool:
    return bool(
        ruleset is not None
        and str(ruleset.code) == TEST_GAME_RULESET_CODE
        and str(ruleset.version or "") == TEST_GAME_RULESET_VERSION
    )


def preferred_default_ruleset() -> Ruleset | None:
    preferred = (
        db.session.query(Ruleset)
        .filter_by(code=TEST_GAME_RULESET_CODE, version=TEST_GAME_RULESET_VERSION, is_active=True)
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


def _object_type_map() -> Dict[str, ObjectType]:
    rows = db.session.query(ObjectType).all()
    return {row.code: row for row in rows}


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _legacy_item_to_payload(item: Dict[str, Any]) -> Dict[str, Any] | None:
    kind = _norm(item.get("kind"))
    code = LOT_KIND_TO_OBJECT_CODE.get(kind)
    if not code:
        return None
    meta = dict(item.get("meta", {}) or {})
    overrides: Dict[str, Any] = {}
    if meta.get("connection_point"):
        overrides["connection_point"] = str(meta["connection_point"]).strip().upper()
    if meta.get("secondary_connection_point"):
        overrides["secondary_connection_point"] = str(meta["secondary_connection_point"]).strip().upper()
    if code == "wind":
        overrides.setdefault("wind_channel", "wind_main")
    return {
        "object_type_code": code,
        "quantity": max(1, int(item.get("qty", item.get("quantity", 1)) or 1)),
        "overrides": overrides,
    }


def load_lot_payloads_from_dir(path: Path) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    if not path.exists():
        return payloads
    for file_path in sorted(path.glob("*.json")):
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        items = []
        for item in list(raw.get("items") or []):
            if not isinstance(item, dict):
                continue
            prepared = _legacy_item_to_payload(item)
            if prepared is not None:
                items.append(prepared)
        if not items:
            continue
        payloads.append(
            {
                "title": str(raw.get("title") or file_path.stem),
                "note": str(raw.get("note") or "Импортировано из legacy fixtures в режим ИЭС 2026."),
                "items": items,
                "suggested_bid": float(raw.get("suggested_bid", 0.0) or 0.0),
            }
        )
    return payloads


def load_test_game_lot_payloads() -> List[Dict[str, Any]]:
    rng = random.Random(TEST_GAME_GENERATED_LOTS_SEED)
    payloads: List[Dict[str, Any]] = []
    for index, spec in enumerate(LOT_PATTERNS, start=1):
        items = []
        total_bid = 0.0
        for item_index, (code, quantity) in enumerate(spec["items"], start=1):
            current_bid = {
                "house_a": 6.0,
                "house_b": 6.5,
                "office": 7.3,
                "factory": 8.5,
                "hospital": 10.0,
                "solar": 6.4,
                "wind": 7.0,
                "storage": 5.2,
                "mini_substation": 4.6,
                "main_substation": 0.0,
            }.get(code, 5.0)
            total_bid += current_bid * quantity
            overrides: Dict[str, Any] = {}
            if code == "wind":
                overrides["wind_channel"] = "wind_main" if item_index % 2 else "wind_west"
            if code in {"factory", "hospital"}:
                overrides["secondary_connection_point"] = "B"
            items.append(
                {
                    "object_type_code": code,
                    "quantity": quantity,
                    "overrides": overrides,
                }
            )
        payloads.append(
            {
                "title": spec["title"],
                "note": "Набор для демонстрации правил ИЭС 2026.",
                "items": items,
                "suggested_bid": round(total_bid * rng.uniform(0.95, 1.08), 2),
            }
        )
    return payloads


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
        lot_name = str(payload.get("title") or "").strip()
        if not lot_name:
            report["skipped"].append("lot payload without title")
            continue
        if db.session.query(Lot).filter_by(session_id=session.id, name=lot_name).first() is not None:
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
            code = str(item.get("object_type_code") or "").strip()
            if code not in resolved_types:
                report["skipped"].append(f"lot {lot_name} item {idx}: unknown object_type_code={code}")
                continue
            db.session.add(
                LotItem(
                    lot_id=lot.id,
                    object_type_id=resolved_types[code].id,
                    quantity=max(1, int(item.get("quantity", 1) or 1)),
                    overrides_json=dict(item.get("overrides", {}) or {}),
                )
            )
            report["lot_items_created"] += 1
    return report


def bootstrap_test_game_session(session: GameSession, *, commit: bool = True) -> Dict[str, Any]:
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
    lot_report = add_lot_payloads_to_session(session=session, payloads=payloads)
    report["lots_created"] = int(lot_report.get("lots_created", 0) or 0)
    report["lot_items_created"] = int(lot_report.get("lot_items_created", 0) or 0)
    skipped = list(lot_report.get("skipped") or [])
    if skipped:
        raise ValueError("Не удалось полностью инициализировать тестовую игру: " + "; ".join(skipped))
    if commit:
        db.session.commit()
    return report
