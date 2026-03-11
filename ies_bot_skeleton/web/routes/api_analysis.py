from __future__ import annotations

import json
from typing import Any, Dict

from flask import Response, jsonify, request
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Forecast, GameSession, Lot, ObjectInstance, ObjectType, Ruleset
from ..services.analysis_context import session_analysis_settings, update_session_analysis_settings
from ..services.corridor import ruleset_default_corridor_settings
from ..services.evaluation import compare_lots, evaluate_lot, recommend_best_lot, strategy_fit
from ..services.forecast_service import parse_and_store_forecast, summarize_forecast
from ..services.session_io import (
    export_evaluations_csv,
    export_session_payload,
    import_session_payload,
)
from ..services.start_pack import apply_start_pack_template_to_session
from .api_support import (
    get_lot_or_404,
    get_session_or_404,
    json_payload,
    lot_items_from_payload,
    value_error_response,
)
from .shared import api_bp


@api_bp.errorhandler(ValueError)
def _value_error(exc: ValueError):
    return value_error_response(exc)


@api_bp.get("/sessions")
@login_required
def list_sessions():
    rows = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/sessions")
@login_required
def create_session():
    payload = json_payload()
    title = str(payload.get("title", "Новая сессия")).strip() or "Новая сессия"
    ruleset_id = payload.get("ruleset_id")
    ruleset: Ruleset | None = None
    if ruleset_id is None:
        ruleset = db.session.query(Ruleset).filter_by(is_active=True).first()
        if ruleset is None:
            raise ValueError("Нет активного набора правил")
        ruleset_id = ruleset.id
    else:
        ruleset = db.session.get(Ruleset, int(ruleset_id))
        if ruleset is None:
            raise ValueError(f"Ruleset {ruleset_id} not found")

    cfg = dict((ruleset.config_json or {}) if ruleset is not None else {})
    auction_cfg = dict(cfg.get("auction", {}) or {})
    default_budget = float(auction_cfg.get("starting_budget", 200.0) or 200.0)
    budget_raw = payload.get("budget_total", None)
    budget_value = float(default_budget if budget_raw is None else budget_raw)

    row = GameSession(
        title=title,
        ruleset_id=int(ruleset_id),
        selected_strategy=str(payload.get("selected_strategy", "balanced")),
        analysis_mode=str(payload.get("analysis_mode", "no_forecast") or "no_forecast"),
        corridor_settings_json=dict(
            payload.get("corridor_settings") or ruleset_default_corridor_settings(cfg)
        ),
        budget_total=budget_value,
        allpay_spent=float(payload.get("allpay_spent", 0.0) or 0.0),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/sessions/<int:session_id>")
@login_required
def get_session(session_id: int):
    row = get_session_or_404(session_id)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/sessions/<int:session_id>/analysis-settings")
@login_required
def get_analysis_settings(session_id: int):
    row = get_session_or_404(session_id)
    return jsonify({"ok": True, "item": session_analysis_settings(row)})


@api_bp.put("/sessions/<int:session_id>/analysis-settings")
@login_required
def update_analysis_settings_endpoint(session_id: int):
    row = get_session_or_404(session_id)
    payload = json_payload()
    updated = update_session_analysis_settings(row, payload)
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": updated})


@api_bp.delete("/sessions/<int:session_id>")
@login_required
def delete_session(session_id: int):
    row = get_session_or_404(session_id)
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.post("/sessions/<int:session_id>/add-start-pack")
@login_required
def add_start_pack(session_id: int):
    payload = json_payload()
    session = get_session_or_404(session_id)
    template_id = payload.get("template_id")
    created = apply_start_pack_template_to_session(
        session=session,
        template_id=int(template_id) if template_id is not None else None,
    )
    return jsonify(
        {
            "ok": True,
            "created": [row.to_dict() for row in created],
            "template_id": template_id or session.ruleset.active_start_pack_template_id,
        }
    )


@api_bp.post("/sessions/import")
@login_required
def import_session():
    payload = json_payload()
    row = import_session_payload(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/sessions/<int:session_id>/export.json")
@login_required
def export_session(session_id: int):
    row = get_session_or_404(session_id)
    return jsonify({"ok": True, "item": export_session_payload(row)})


@api_bp.get("/sessions/<int:session_id>/evaluations.csv")
@login_required
def export_evaluations(session_id: int):
    row = get_session_or_404(session_id)
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
    include_inactive = bool(request.args.get("include_inactive", type=int))
    if include_inactive and current_user.role != "admin":
        include_inactive = False
    query = db.session.query(ObjectType)
    if not include_inactive:
        query = query.filter_by(is_active=True)
    rows = query.order_by(ObjectType.category, ObjectType.code).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


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
    payload = json_payload()
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
    payload = json_payload()
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
    payload = json_payload()
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

    lot_items_from_payload(lot, payload.get("items", []) or [])

    db.session.add(lot)
    db.session.commit()
    return jsonify({"ok": True, "item": lot.to_dict()})


@api_bp.put("/lots/<int:lot_id>")
@login_required
def update_lot(lot_id: int):
    lot = get_lot_or_404(lot_id)
    payload = json_payload()
    for key in ("name", "scope", "status", "note"):
        if key in payload:
            setattr(lot, key, str(payload[key]))
    for key in ("base_bid", "current_bid"):
        if key in payload:
            setattr(lot, key, float(payload[key] or 0.0))
    if "available_round" in payload:
        lot.available_round = int(payload["available_round"] or 1)
    if "items" in payload:
        lot_items_from_payload(lot, payload.get("items", []) or [])

    db.session.add(lot)
    db.session.commit()
    return jsonify({"ok": True, "item": lot.to_dict()})


@api_bp.delete("/lots/<int:lot_id>")
@login_required
def delete_lot(lot_id: int):
    lot = get_lot_or_404(lot_id)
    db.session.delete(lot)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.post("/lots/<int:lot_id>/evaluate")
@login_required
def evaluate_one_lot(lot_id: int):
    payload = json_payload()
    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)

    mode = str(payload.get("mode", session.analysis_mode or "forecast"))
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
        corridor_override=payload.get("corridor_override"),
        persist=True,
    )
    return jsonify({"ok": True, "item": out})


@api_bp.post("/lots/compare")
@login_required
def compare_lots_endpoint():
    payload = json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    lot_ids = [int(x) for x in payload.get("lot_ids", []) or []]
    mode = str(payload.get("mode", "forecast"))
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")
    if len(lot_ids) < 2:
        raise ValueError("lot_ids должен содержать минимум 2 лота")

    session = get_session_or_404(session_id)
    lots = db.session.query(Lot).filter(Lot.id.in_(lot_ids), Lot.session_id == session_id).all()
    if len(lots) < 2:
        raise ValueError("Лоты не найдены")

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = compare_lots(
        session=session,
        lots=lots,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
        corridor_override=payload.get("corridor_override"),
    )
    return jsonify({"ok": True, "items": out})


@api_bp.post("/forecast/upload")
@login_required
def upload_forecast():
    session_id = request.form.get("session_id", type=int)
    if not session_id:
        raise ValueError("session_id обязателен")
    session = get_session_or_404(session_id)
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
    if session.selected_forecast_id is None:
        session.selected_forecast_id = forecast.id
        db.session.add(session)
        db.session.commit()

    return jsonify(
        {
            "ok": True,
            "item": forecast.to_dict(),
            "diagnostics": diag.to_dict(),
            "summary": summarize_forecast(forecast),
        }
    )


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
                "summary": summarize_forecast(forecast),
            },
        }
    )


@api_bp.post("/forecast/<int:forecast_id>/analyze")
@login_required
def analyze_forecast(forecast_id: int):
    forecast = db.session.get(Forecast, forecast_id)
    if forecast is None:
        raise ValueError(f"Forecast {forecast_id} not found")
    return jsonify({"ok": True, "item": summarize_forecast(forecast)})


@api_bp.post("/recommend/best-lot")
@login_required
def recommend_best():
    payload = json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    mode = str(payload.get("mode", "forecast"))
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")

    session = get_session_or_404(session_id)
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
        corridor_override=payload.get("corridor_override"),
    )
    return jsonify({"ok": True, "item": out})


@api_bp.post("/recommend/strategy-fit")
@login_required
def recommend_strategy_fit():
    payload = json_payload()
    lot_id = int(payload.get("lot_id", 0) or 0)
    mode = str(payload.get("mode", "forecast"))
    if lot_id <= 0:
        raise ValueError("lot_id обязателен")

    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = strategy_fit(
        session=session,
        lot=lot,
        mode=mode,
        forecast=forecast,
        corridor_override=payload.get("corridor_override"),
    )
    return jsonify({"ok": True, "item": out})
