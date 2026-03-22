from __future__ import annotations

import json
import random
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
    "mini_substation_b",
    "cyber_solar",
    "solar",
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
TEST_GAME_GENERATED_LOTS_COUNT = 20
TEST_GAME_GENERATED_LOTS_SEED = 20260322

GENERATED_LOT_TITLE_ADJECTIVES = (
    "Северный",
    "Южный",
    "Янтарный",
    "Тихий",
    "Резервный",
    "Гибкий",
    "Пиковый",
    "Опорный",
    "Городской",
    "Речной",
    "Степной",
    "Полярный",
    "Индустриальный",
    "Сетевой",
    "Балансовый",
    "Маневровый",
    "Транзитный",
    "Локальный",
    "Контурный",
    "Энергетический",
)
GENERATED_LOT_TITLE_SUFFIXES = (
    "контур",
    "узел",
    "кластер",
    "пакет",
    "лот",
    "канал",
    "блок",
    "комплект",
    "резерв",
    "модуль",
    "контракт",
    "микс",
    "альянс",
    "профиль",
    "пул",
    "каскад",
    "фидер",
    "баланс",
    "сегмент",
    "переход",
)
GENERATED_LOT_PATTERNS: Sequence[Dict[str, Any]] = (
    {"title": "Солнечный квартал", "items": (("houseA", (1, 2)), ("solarRobot", (1, 2)))},
    {"title": "Офисный узел", "items": (("office", (1, 2)), ("miniA", (1, 1)))},
    {"title": "Ветропром", "items": (("factory", (1, 1)), ("wind", (1, 2)))},
    {"title": "Сетевой резерв", "items": (("storage", (1, 2)), ("miniB", (1, 1)))},
    {"title": "Тепловой узел", "items": (("tps", (1, 1)), ("main", (1, 1)))},
    {"title": "Офисный резерв", "items": (("office", (1, 1)), ("storage", (1, 2)))},
    {"title": "Жилой буфер", "items": (("houseA", (1, 3)), ("storage", (1, 1)))},
    {"title": "Промышленная генерация", "items": (("factory", (1, 1)), ("tps", (1, 1)))},
    {"title": "Ветровой буфер", "items": (("wind", (1, 2)), ("storage", (1, 2)))},
    {"title": "Солнечный буфер", "items": (("solarRobot", (1, 2)), ("storage", (1, 2)))},
    {"title": "Магистральный офис", "items": (("main", (1, 1)), ("office", (1, 1)))},
    {"title": "Промышленный фидер", "items": (("miniB", (1, 1)), ("factory", (1, 1)))},
    {"title": "Жилой ветер", "items": (("houseB", (1, 2)), ("wind", (1, 1)))},
    {"title": "Солнечная сеть", "items": (("miniA", (1, 1)), ("solarRobot", (1, 2)))},
    {"title": "Тепловой резерв", "items": (("tps", (1, 1)), ("storage", (1, 1)))},
    {
        "title": "Смешанный квартал",
        "items": (("houseA", (1, 2)), ("office", (1, 1)), ("miniA", (1, 1))),
    },
    {"title": "Индустриальный буфер", "items": (("factory", (1, 1)), ("storage", (1, 2)))},
    {"title": "Маневровая СЭС", "items": (("solar", (1, 2)), ("storage", (1, 1)))},
    {"title": "Транзитный ветер", "items": (("main", (1, 1)), ("wind", (1, 1)))},
    {
        "title": "Гибкий офис",
        "items": (("office", (1, 1)), ("solarRobot", (1, 1)), ("storage", (1, 1))),
    },
)
GENERATED_LOT_BID_WEIGHTS = {
    "housea": 4.0,
    "houseb": 4.0,
    "office": 5.0,
    "factory": 7.0,
    "solarrobot": 6.0,
    "solar": 6.5,
    "wind": 6.5,
    "tps": 9.0,
    "storage": 5.5,
    "main": 8.0,
    "minia": 4.5,
    "minib": 5.0,
}

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
    "solar": "solar",
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
    return [
        *load_lot_payloads_from_paths(TEST_GAME_LOT_PATHS),
        *_generated_test_game_lot_payloads(),
    ]


def _generated_test_game_lot_payloads() -> List[Dict[str, Any]]:
    rng = random.Random(TEST_GAME_GENERATED_LOTS_SEED)
    payloads: List[Dict[str, Any]] = []
    used_titles: set[str] = set()
    connection_points = ("A", "B", "C", "D", "E", "F")
    for index, spec in enumerate(GENERATED_LOT_PATTERNS[:TEST_GAME_GENERATED_LOTS_COUNT], start=1):
        title = (
            f"{spec['title']} "
            f"{GENERATED_LOT_TITLE_ADJECTIVES[index - 1].lower()} "
            f"{GENERATED_LOT_TITLE_SUFFIXES[index - 1]}"
        )
        if title in used_titles:
            title = f"{title} #{index}"
        used_titles.add(title)

        items_payload: List[Dict[str, Any]] = []
        bid_weight = 0.0
        note_bits: List[str] = []
        for item_index, (kind, qty_range) in enumerate(spec["items"], start=1):
            quantity = rng.randint(int(qty_range[0]), int(qty_range[1]))
            normalized_kind = _norm(kind)
            bid_weight += float(GENERATED_LOT_BID_WEIGHTS.get(normalized_kind, 4.0)) * quantity
            note_bits.append(f"{kind} x{quantity}")
            items_payload.append(
                {
                    "kind": str(kind),
                    "id": f"g{index:02d}_{normalized_kind}_{item_index}",
                    "qty": int(quantity),
                    "contract_rub_per_tick": round(rng.uniform(0.0, 2.5), 1),
                    "tariff_rub_per_mw_tick": round(rng.uniform(0.0, 6.0), 1),
                    "meta": {"connection_point": connection_points[(index + item_index - 2) % 6]},
                }
            )

        suggested_bid = round(max(7.0, bid_weight * rng.uniform(0.82, 1.16)), 1)
        payloads.append(
            {
                "lot_id": f"G{index:02d}",
                "title": title,
                "note": " · ".join(note_bits),
                "items": items_payload,
                "suggested_bid": float(suggested_bid),
            }
        )
    return payloads


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
