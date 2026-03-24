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
    "Главная подстанция, мини-подстанция нагрузки, базовый дом и демонстрационная СЭС "
    "по правилам ИЭС 2026."
)

TEST_GAME_DEFAULT_SESSION_TITLE = "Тестовая игра"
TEST_GAME_BUNDLED_FORECAST_NAME = "Прогноз тестовой игры"
TEST_GAME_FORECAST_SOURCE_LABEL = "Встроенный прогноз ИЭС 2026"
TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME = "Прогноз игры"

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
LOT_BLUEPRINTS: Sequence[Dict[str, Any]] = (
    {"lot_id": "G01", "title": "Жилой север", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "load_north"}, {"code": "house_a", "qty": (1, 2), "district": "load_north"}, {"code": "house_b", "qty": (0, 1), "district": "load_north"}]},
    {"lot_id": "G02", "title": "Жилой юг", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "load_south"}, {"code": "house_b", "qty": (1, 2), "district": "load_south"}, {"code": "house_a", "qty": (0, 1), "district": "load_south"}]},
    {"lot_id": "G03", "title": "Офисный квартал", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "office_central"}, {"code": "office", "qty": (1, 2), "district": "office_central"}, {"code": "storage", "qty": (0, 1), "district": "office_central"}]},
    {"lot_id": "G04", "title": "Промышленная линия", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "industrial_east"}, {"code": "factory", "qty": (1, 1), "district": "industrial_east"}, {"code": "storage", "qty": (0, 1), "district": "industrial_east"}]},
    {"lot_id": "G05", "title": "Медицинский кластер", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "critical_med"}, {"code": "hospital", "qty": (1, 1), "district": "critical_med"}, {"code": "storage", "qty": (0, 1), "district": "critical_med"}]},
    {"lot_id": "G06", "title": "Солнечный пояс", "items": [{"code": "mini_substation", "qty": (0, 1), "district": "gen_solar_north"}, {"code": "solar", "qty": (1, 2), "district": "gen_solar_north"}]},
    {"lot_id": "G07", "title": "Солнечный парк", "items": [{"code": "solar", "qty": (1, 2), "district": "gen_solar_west"}, {"code": "storage", "qty": (0, 1), "district": "gen_solar_west"}]},
    {"lot_id": "G08", "title": "Ветровой запад", "items": [{"code": "mini_substation", "qty": (0, 1), "district": "gen_wind_west"}, {"code": "wind", "qty": (1, 2), "district": "gen_wind_west", "wind_channels": ("wind_west",)}]},
    {"lot_id": "G09", "title": "Ветровой север", "items": [{"code": "wind", "qty": (1, 2), "district": "gen_wind_north", "wind_channels": ("wind_main", "wind_west")}, {"code": "storage", "qty": (0, 1), "district": "gen_wind_north"}]},
    {"lot_id": "G10", "title": "Буфер дефицита", "items": [{"code": "storage", "qty": (1, 2), "district": "buffer_north"}, {"code": "mini_substation", "qty": (0, 1), "district": "buffer_north"}]},
    {"lot_id": "G11", "title": "Резерв антидемпинга", "items": [{"code": "storage", "qty": (1, 2), "district": "buffer_market"}, {"code": "wind", "qty": (0, 1), "district": "gen_buffer_market", "wind_channels": ("wind_main",)}]},
    {"lot_id": "G12", "title": "Нагрузочный резерв", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "load_reserve"}, {"code": "house_a", "qty": (1, 1), "district": "load_reserve"}, {"code": "office", "qty": (1, 1), "district": "load_reserve"}]},
    {"lot_id": "G13", "title": "Индустриальный дублёр", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "industrial_backup"}, {"code": "factory", "qty": (1, 1), "district": "industrial_backup"}, {"code": "office", "qty": (0, 1), "district": "industrial_backup"}]},
    {"lot_id": "G14", "title": "Критическая поддержка", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "critical_support"}, {"code": "hospital", "qty": (1, 1), "district": "critical_support"}, {"code": "solar", "qty": (0, 1), "district": "gen_critical_support"}]},
    {"lot_id": "G15", "title": "ВИЭ с буфером", "items": [{"code": "solar", "qty": (1, 1), "district": "gen_mix_east"}, {"code": "wind", "qty": (1, 1), "district": "gen_mix_east", "wind_channels": ("wind_main", "wind_west")}, {"code": "storage", "qty": (1, 1), "district": "gen_mix_east"}]},
    {"lot_id": "G16", "title": "Городской контур", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "urban_loop"}, {"code": "house_a", "qty": (1, 2), "district": "urban_loop"}, {"code": "office", "qty": (1, 1), "district": "urban_loop"}]},
    {"lot_id": "G17", "title": "Смена нагрузки", "items": [{"code": "house_b", "qty": (1, 2), "district": "load_shift"}, {"code": "storage", "qty": (1, 1), "district": "load_shift"}, {"code": "mini_substation", "qty": (0, 1), "district": "load_shift"}]},
    {"lot_id": "G18", "title": "Генерация на экспорт", "items": [{"code": "solar", "qty": (1, 2), "district": "export_gen"}, {"code": "wind", "qty": (0, 1), "district": "export_gen", "wind_channels": ("wind_main", "wind_west")}]},
    {"lot_id": "G19", "title": "Ветер и накопитель", "items": [{"code": "wind", "qty": (1, 1), "district": "wind_storage", "wind_channels": ("wind_west",)}, {"code": "storage", "qty": (1, 1), "district": "wind_storage"}, {"code": "mini_substation", "qty": (0, 1), "district": "wind_storage"}]},
    {"lot_id": "G20", "title": "Потребительский пакет", "items": [{"code": "house_a", "qty": (1, 1), "district": "consumer_pack"}, {"code": "house_b", "qty": (1, 1), "district": "consumer_pack"}, {"code": "office", "qty": (1, 1), "district": "consumer_pack"}, {"code": "mini_substation", "qty": (1, 1), "district": "consumer_pack"}]},
    {"lot_id": "L01", "title": "Локальная ветка нагрузки", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "local_load_1"}, {"code": "house_a", "qty": (1, 1), "district": "local_load_1"}]},
    {"lot_id": "L02", "title": "Локальная солнечная точка", "items": [{"code": "solar", "qty": (1, 1), "district": "local_gen_1"}, {"code": "mini_substation", "qty": (0, 1), "district": "local_gen_1"}]},
    {"lot_id": "L03", "title": "Локальный ветер", "items": [{"code": "wind", "qty": (1, 1), "district": "local_gen_2", "wind_channels": ("wind_west",)}, {"code": "storage", "qty": (0, 1), "district": "local_gen_2"}]},
    {"lot_id": "L04", "title": "Локальный буфер", "items": [{"code": "storage", "qty": (1, 1), "district": "local_buffer"}, {"code": "mini_substation", "qty": (0, 1), "district": "local_buffer"}]},
    {"lot_id": "L05", "title": "Локальная критическая точка", "items": [{"code": "mini_substation", "qty": (1, 1), "district": "local_critical"}, {"code": "hospital", "qty": (1, 1), "district": "local_critical"}]},
)

BASE_TARIFF_BY_CODE: Dict[str, float] = {
    "house_a": 6.2,
    "house_b": 6.8,
    "office": 7.5,
    "factory": 8.2,
    "hospital": 9.8,
    "solar": 6.5,
    "wind": 7.1,
    "storage": 5.4,
    "mini_substation": 4.3,
    "main_substation": 0.0,
}


def _lot_item_payload(
    *,
    rng: random.Random,
    code: str,
    quantity: int,
    district: str,
    wind_channels: Sequence[str] | None = None,
) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {"district": district}
    if code == "wind":
        channels = list(wind_channels or ("wind_main", "wind_west"))
        overrides["wind_channel"] = channels[rng.randrange(len(channels))]
    if code == "factory":
        overrides["secondary_connection_point"] = "B"
    if code == "hospital":
        overrides["secondary_connection_point"] = "B"
    return {
        "object_type_code": code,
        "quantity": max(1, quantity),
        "overrides": overrides,
    }


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
    for spec in LOT_BLUEPRINTS:
        items = []
        total_bid = 0.0
        for item_spec in spec["items"]:
            min_qty, max_qty = tuple(item_spec.get("qty", (1, 1)))
            quantity = rng.randint(int(min_qty), int(max_qty))
            if quantity <= 0:
                continue
            code = str(item_spec["code"])
            total_bid += BASE_TARIFF_BY_CODE.get(code, 5.0) * quantity
            items.append(
                _lot_item_payload(
                    rng=rng,
                    code=code,
                    quantity=quantity,
                    district=str(item_spec.get("district") or "default"),
                    wind_channels=item_spec.get("wind_channels"),
                )
            )
        if not items:
            continue
        note_scope = "global" if str(spec["lot_id"]).startswith("G") else "local"
        payloads.append(
            {
                "lot_id": str(spec["lot_id"]),
                "title": f"{spec['lot_id']} · {spec['title']}",
                "note": (
                    "Детерминированно-случайный лот тестовой игры ИЭС 2026. "
                    f"Scope={note_scope}, bundle valuation и topology planning обязательны."
                ),
                "scope": note_scope,
                "items": items,
                "suggested_bid": round(total_bid * rng.uniform(0.94, 1.08), 2),
            }
        )
    for payload in payloads:
        if str(payload.get("lot_id") or "") != "L03":
            continue
        payload.update(
            {
                "title": "Солнечный накопитель",
                "note": "Солнечная панель + накопитель из расширенного тестового пула.",
                "scope": "normal",
                "items": [
                    {
                        "object_type_code": "solar",
                        "quantity": 1,
                        "overrides": {
                            "connection_point": "B",
                            "district": "local_gen_2",
                            "generation_mw": 3.2,
                            "contract_rub_per_tick": 2.0,
                        },
                    },
                    {
                        "object_type_code": "storage",
                        "quantity": 1,
                        "overrides": {
                            "connection_point": "B",
                            "district": "local_gen_2",
                            "capacity_mw_tick": 16.0,
                            "charge_rate_mw_tick": 4.0,
                            "discharge_rate_mw_tick": 4.0,
                            "contract_rub_per_tick": 3.0,
                        },
                    },
                ],
                "suggested_bid": 13.0,
            }
        )
        break
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
            scope=str(payload.get("scope") or "normal"),
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
