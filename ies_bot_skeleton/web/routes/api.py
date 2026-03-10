from __future__ import annotations

import json
from typing import Any, Dict, List

from flask import Blueprint, Response, jsonify, request
from flask_login import login_required

from ..extensions import db
from ..models import (
    Forecast,
    GameSession,
    Lot,
    LotItem,
    ObjectInstance,
    ObjectType,
    Ruleset,
)
from ..services.auth import role_required
from ..services.evaluation import compare_lots, evaluate_lot, recommend_best_lot, strategy_fit
from ..services.forecast_service import parse_and_store_forecast
from ..services.legacy_import import import_legacy_data
from ..services.seed import START_PACK_CODES
from ..services.session_io import (
    export_evaluations_csv,
    export_session_payload,
    import_session_payload,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _json_payload() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _get_session_or_404(session_id: int) -> GameSession:
    row = db.session.get(GameSession, session_id)
    if row is None:
        raise ValueError(f"Session {session_id} not found")
    return row


def _get_lot_or_404(lot_id: int) -> Lot:
    row = db.session.get(Lot, lot_id)
    if row is None:
        raise ValueError(f"Lot {lot_id} not found")
    return row


def _lot_items_from_payload(lot: Lot, items_payload: List[Dict[str, Any]]) -> None:
    lot.items.clear()
    for row in items_payload:
        object_type_id = row.get("object_type_id")
        if object_type_id is None and row.get("object_type_code"):
            obj = (
                db.session.query(ObjectType)
                .filter_by(code=str(row["object_type_code"]))
                .one_or_none()
            )
            if obj is not None:
                object_type_id = obj.id
        if object_type_id is None:
            continue
        quantity = max(1, int(row.get("quantity", 1) or 1))
        item = LotItem(
            object_type_id=int(object_type_id),
            quantity=quantity,
            overrides_json=dict(row.get("overrides", {})),
        )
        lot.items.append(item)


@api_bp.errorhandler(ValueError)
def _value_error(exc: ValueError):
    return jsonify({"ok": False, "error": str(exc)}), 400


@api_bp.get("/sessions")
@login_required
def list_sessions():
    rows = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/sessions")
@login_required
def create_session():
    payload = _json_payload()
    title = str(payload.get("title", "Новая сессия")).strip() or "Новая сессия"
    ruleset_id = payload.get("ruleset_id")
    if ruleset_id is None:
        ruleset = db.session.query(Ruleset).filter_by(is_active=True).first()
        if ruleset is None:
            raise ValueError("Нет активного ruleset")
        ruleset_id = ruleset.id

    row = GameSession(
        title=title,
        ruleset_id=int(ruleset_id),
        selected_strategy=str(payload.get("selected_strategy", "balanced")),
        budget_total=float(payload.get("budget_total", 9999.0) or 9999.0),
        allpay_spent=float(payload.get("allpay_spent", 0.0) or 0.0),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/sessions/<int:session_id>")
@login_required
def get_session(session_id: int):
    row = _get_session_or_404(session_id)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.delete("/sessions/<int:session_id>")
@login_required
def delete_session(session_id: int):
    row = _get_session_or_404(session_id)
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.post("/sessions/<int:session_id>/add-start-pack")
@login_required
def add_start_pack(session_id: int):
    session = _get_session_or_404(session_id)
    existing = (
        db.session.query(ObjectInstance)
        .filter_by(session_id=session.id, is_from_start_pack=True)
        .count()
    )
    if existing > 0:
        raise ValueError("Стартовый пакет уже добавлен")

    types = {
        row.code: row
        for row in db.session.query(ObjectType).filter(ObjectType.code.in_(START_PACK_CODES)).all()
    }

    missing = [code for code in START_PACK_CODES if code not in types]
    if missing:
        raise ValueError(f"В справочнике нет типов стартового пакета: {', '.join(missing)}")

    main_obj = ObjectInstance(
        session_id=session.id,
        object_type_id=types["main_substation"].id,
        custom_name="Main Substation",
        current_parameters_json={},
        is_from_start_pack=True,
        district="core",
        is_active=True,
    )
    db.session.add(main_obj)
    db.session.flush()

    mini_obj = ObjectInstance(
        session_id=session.id,
        object_type_id=types["mini_substation_a"].id,
        custom_name="Mini A",
        current_parameters_json={},
        is_from_start_pack=True,
        parent_instance_id=main_obj.id,
        district="core",
        is_active=True,
    )
    db.session.add(mini_obj)
    db.session.flush()

    solar_obj = ObjectInstance(
        session_id=session.id,
        object_type_id=types["cyber_solar"].id,
        custom_name="Cyber SES",
        current_parameters_json={},
        is_from_start_pack=True,
        parent_instance_id=mini_obj.id,
        district="core",
        is_active=True,
    )
    house_obj = ObjectInstance(
        session_id=session.id,
        object_type_id=types["house"].id,
        custom_name="House",
        current_parameters_json={},
        is_from_start_pack=True,
        parent_instance_id=mini_obj.id,
        district="core",
        is_active=True,
    )
    db.session.add(solar_obj)
    db.session.add(house_obj)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "created": [
                main_obj.to_dict(),
                mini_obj.to_dict(),
                solar_obj.to_dict(),
                house_obj.to_dict(),
            ],
        }
    )


@api_bp.post("/sessions/import")
@login_required
def import_session():
    payload = _json_payload()
    row = import_session_payload(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/sessions/<int:session_id>/export.json")
@login_required
def export_session(session_id: int):
    row = _get_session_or_404(session_id)
    return jsonify({"ok": True, "item": export_session_payload(row)})


@api_bp.get("/sessions/<int:session_id>/evaluations.csv")
@login_required
def export_evaluations(session_id: int):
    row = _get_session_or_404(session_id)
    payload = export_evaluations_csv(row)
    return Response(
        payload,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=session_{session_id}_evaluations.csv"
        },
    )


@api_bp.get("/object-types")
@login_required
def list_object_types():
    rows = db.session.query(ObjectType).filter_by(is_active=True).order_by(ObjectType.code).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/object-types")
@login_required
@role_required("admin")
def create_object_type():
    payload = _json_payload()
    row = ObjectType(
        code=str(payload.get("code", "")).strip(),
        name=str(payload.get("name", "")).strip(),
        category=str(payload.get("category", "infrastructure")),
        subtype=str(payload.get("subtype", "")),
        description=str(payload.get("description", "")),
        default_parameters_json=dict(payload.get("default_parameters", {})),
        editable_fields_json=list(payload.get("editable_fields", [])),
        rules_json=dict(payload.get("rules", {})),
        is_active=bool(payload.get("is_active", True)),
    )
    if not row.code or not row.name:
        raise ValueError("code и name обязательны")
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def update_object_type(object_type_id: int):
    row = db.session.get(ObjectType, object_type_id)
    if row is None:
        raise ValueError(f"ObjectType {object_type_id} not found")
    payload = _json_payload()
    for key in ("name", "category", "subtype", "description"):
        if key in payload:
            setattr(row, key, str(payload[key]))
    if "default_parameters" in payload:
        row.default_parameters_json = dict(payload["default_parameters"] or {})
    if "editable_fields" in payload:
        row.editable_fields_json = list(payload["editable_fields"] or [])
    if "rules" in payload:
        row.rules_json = dict(payload["rules"] or {})
    if "is_active" in payload:
        row.is_active = bool(payload["is_active"])
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.delete("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def delete_object_type(object_type_id: int):
    row = db.session.get(ObjectType, object_type_id)
    if row is None:
        raise ValueError(f"ObjectType {object_type_id} not found")
    row.is_active = False
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.get("/objects")
@login_required
def list_objects():
    session_id = request.args.get("session_id", type=int)
    if not session_id:
        raise ValueError("session_id обязателен")
    rows = (
        db.session.query(ObjectInstance)
        .filter_by(session_id=session_id)
        .order_by(ObjectInstance.id)
        .all()
    )
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/objects")
@login_required
def create_object():
    payload = _json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    object_type_id = int(payload.get("object_type_id", 0) or 0)
    if session_id <= 0 or object_type_id <= 0:
        raise ValueError("session_id и object_type_id обязательны")

    row = ObjectInstance(
        session_id=session_id,
        object_type_id=object_type_id,
        custom_name=str(payload.get("custom_name", "")),
        current_parameters_json=dict(payload.get("current_parameters", {})),
        source_lot_id=payload.get("source_lot_id"),
        is_from_start_pack=bool(payload.get("is_from_start_pack", False)),
        parent_instance_id=payload.get("parent_instance_id"),
        district=str(payload.get("district", "default")),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/objects/<int:object_id>")
@login_required
def update_object(object_id: int):
    row = db.session.get(ObjectInstance, object_id)
    if row is None:
        raise ValueError(f"Object {object_id} not found")
    payload = _json_payload()
    for key in ("custom_name", "district"):
        if key in payload:
            setattr(row, key, str(payload[key]))
    if "parent_instance_id" in payload:
        row.parent_instance_id = payload["parent_instance_id"]
    if "current_parameters" in payload:
        row.current_parameters_json = dict(payload["current_parameters"] or {})
    if "is_active" in payload:
        row.is_active = bool(payload["is_active"])
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.delete("/objects/<int:object_id>")
@login_required
def delete_object(object_id: int):
    row = db.session.get(ObjectInstance, object_id)
    if row is None:
        raise ValueError(f"Object {object_id} not found")
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.get("/lots")
@login_required
def list_lots():
    session_id = request.args.get("session_id", type=int)
    if not session_id:
        raise ValueError("session_id обязателен")
    rows = db.session.query(Lot).filter_by(session_id=session_id).order_by(Lot.id).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/lots")
@login_required
def create_lot():
    payload = _json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    if session_id <= 0:
        raise ValueError("session_id обязателен")

    lot = Lot(
        session_id=session_id,
        name=str(payload.get("name", "Новый лот")),
        scope=str(payload.get("scope", "normal")),
        status=str(payload.get("status", "available")),
        base_bid=float(payload.get("base_bid", 0.0) or 0.0),
        current_bid=float(payload.get("current_bid", 0.0) or 0.0),
        note=str(payload.get("note", "")),
        available_round=int(payload.get("available_round", 1) or 1),
    )
    db.session.add(lot)
    db.session.flush()

    items_payload = payload.get("items", []) or []
    if not isinstance(items_payload, list):
        raise ValueError("items должен быть массивом")
    _lot_items_from_payload(lot, items_payload)
    if not lot.items:
        raise ValueError("Лот не может быть пустым")

    db.session.add(lot)
    db.session.commit()
    return jsonify({"ok": True, "item": lot.to_dict()})


@api_bp.put("/lots/<int:lot_id>")
@login_required
def update_lot(lot_id: int):
    lot = _get_lot_or_404(lot_id)
    payload = _json_payload()
    for key in ("name", "scope", "status", "note"):
        if key in payload:
            setattr(lot, key, str(payload[key]))
    for key in ("base_bid", "current_bid"):
        if key in payload:
            setattr(lot, key, float(payload[key] or 0.0))
    if "available_round" in payload:
        lot.available_round = int(payload["available_round"] or 1)
    if "items" in payload:
        items_payload = payload.get("items", []) or []
        if not isinstance(items_payload, list):
            raise ValueError("items должен быть массивом")
        _lot_items_from_payload(lot, items_payload)
        if not lot.items:
            raise ValueError("Лот не может быть пустым")

    db.session.add(lot)
    db.session.commit()
    return jsonify({"ok": True, "item": lot.to_dict()})


@api_bp.delete("/lots/<int:lot_id>")
@login_required
def delete_lot(lot_id: int):
    lot = _get_lot_or_404(lot_id)
    db.session.delete(lot)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.post("/lots/<int:lot_id>/evaluate")
@login_required
def evaluate_one_lot(lot_id: int):
    payload = _json_payload()
    lot = _get_lot_or_404(lot_id)
    session = _get_session_or_404(lot.session_id)

    mode = str(payload.get("mode", "forecast"))
    strategy = payload.get("strategy")
    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = evaluate_lot(
        session=session,
        lot=lot,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
        persist=True,
    )
    return jsonify({"ok": True, "item": out})


@api_bp.post("/lots/compare")
@login_required
def compare_lots_endpoint():
    payload = _json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    lot_ids = [int(x) for x in payload.get("lot_ids", []) or []]
    mode = str(payload.get("mode", "forecast"))
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")
    if len(lot_ids) < 2:
        raise ValueError("lot_ids должен содержать минимум 2 лота")

    session = _get_session_or_404(session_id)
    lots = db.session.query(Lot).filter(Lot.id.in_(lot_ids), Lot.session_id == session_id).all()
    if not lots:
        raise ValueError("Лоты не найдены")

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = compare_lots(session=session, lots=lots, mode=mode, strategy=strategy, forecast=forecast)
    return jsonify({"ok": True, "items": out})


@api_bp.post("/forecast/upload")
@login_required
def upload_forecast():
    session_id = request.form.get("session_id", type=int)
    if not session_id:
        raise ValueError("session_id обязателен")
    name = (request.form.get("name") or "Forecast").strip() or "Forecast"

    file = request.files.get("file")
    if file is None:
        raise ValueError("Не передан CSV файл")
    content = file.read()

    column_map_raw = request.form.get("column_map")
    column_map = None
    if column_map_raw:
        try:
            column_map = json.loads(column_map_raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Некорректный column_map: {exc}") from exc

    forecast, diag = parse_and_store_forecast(
        session_id=session_id,
        name=name,
        source_file=file.filename or "upload.csv",
        content=content,
        column_map=column_map,
    )

    return jsonify({"ok": True, "item": forecast.to_dict(), "diagnostics": diag.to_dict()})


@api_bp.get("/forecast/<int:forecast_id>")
@login_required
def get_forecast(forecast_id: int):
    forecast = db.session.get(Forecast, forecast_id)
    if forecast is None:
        raise ValueError(f"Forecast {forecast_id} not found")
    return jsonify(
        {
            "ok": True,
            "item": {
                **forecast.to_dict(),
                "periods": [p.to_dict() for p in forecast.periods],
            },
        }
    )


@api_bp.post("/forecast/<int:forecast_id>/analyze")
@login_required
def analyze_forecast(forecast_id: int):
    forecast = db.session.get(Forecast, forecast_id)
    if forecast is None:
        raise ValueError(f"Forecast {forecast_id} not found")

    periods = forecast.periods
    if not periods:
        return jsonify({"ok": True, "item": {"count": 0, "series": {}}})

    def avg(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else None

    summary = {
        "count": len(periods),
        "tick_from": min(p.tick for p in periods),
        "tick_to": max(p.tick for p in periods),
        "avg_wind": avg([p.wind for p in periods]),
        "avg_illumination": avg([p.illumination for p in periods]),
        "avg_market_price": avg([p.market_price for p in periods]),
    }

    return jsonify({"ok": True, "item": summary})


@api_bp.post("/recommend/best-lot")
@login_required
def recommend_best():
    payload = _json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    mode = str(payload.get("mode", "forecast"))
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")

    session = _get_session_or_404(session_id)
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    if not lots:
        raise ValueError("Нет лотов для рекомендации")

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = recommend_best_lot(
        session=session,
        lots=lots,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
    )
    return jsonify({"ok": True, "item": out})


@api_bp.post("/recommend/strategy-fit")
@login_required
def recommend_strategy_fit():
    payload = _json_payload()
    lot_id = int(payload.get("lot_id", 0) or 0)
    mode = str(payload.get("mode", "forecast"))
    if lot_id <= 0:
        raise ValueError("lot_id обязателен")

    lot = _get_lot_or_404(lot_id)
    session = _get_session_or_404(lot.session_id)

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = strategy_fit(session=session, lot=lot, mode=mode, forecast=forecast)
    return jsonify({"ok": True, "item": out})


@api_bp.post("/legacy/import")
@login_required
@role_required("admin")
def legacy_import_endpoint():
    payload = _json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    if session_id <= 0:
        raise ValueError("session_id обязателен")

    report = import_legacy_data(session_id=session_id)
    return jsonify({"ok": True, "item": report})
