from __future__ import annotations

import json

from flask import Response, jsonify, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from ...application.admin import apply_start_pack_to_session
from ...application.context import (
    session_analysis_settings_for_session,
    update_analysis_settings_for_session,
)
from ...application.analysis import compare_session_lots, evaluate_session_lot
from ...application.forecasts import parse_uploaded_forecast, summarize_stored_forecast
from ...application.lots import delete_lot as delete_lot_use_case
from ...application.objects import (
    create_session_object,
    delete_session_object,
    get_session_object_or_error,
    list_session_objects,
    update_session_object,
)
from ...application.recommendations import recommend_for_session, strategy_fit_for_lot
from ...application.sessions import create_session_record
from ..extensions import db
from ..models import Forecast, GameSession, Lot, ObjectType
from ..services.session_io import (
    export_evaluations_csv,
    export_session_payload,
    import_session_payload,
)
from .api_support import (
    ApiError,
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


@api_bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": {
                        400: "bad_request",
                        401: "forbidden",
                        403: "forbidden",
                        404: "not_found",
                    }.get(exc.code or 500, "bad_request"),
                    "message": exc.description,
                    "details": {},
                },
            }
        ),
        exc.code or 500,
    )


@api_bp.get("/sessions")
@login_required
def list_sessions():
    rows = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/sessions")
@login_required
def create_session():
    payload = json_payload()
    row = create_session_record(payload)
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
    return jsonify({"ok": True, "item": session_analysis_settings_for_session(row)})


@api_bp.put("/sessions/<int:session_id>/analysis-settings")
@login_required
def update_analysis_settings_endpoint(session_id: int):
    row = get_session_or_404(session_id)
    payload = json_payload()
    updated = update_analysis_settings_for_session(row, payload)
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
    created = apply_start_pack_to_session(
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
    get_session_or_404(session_id)
    rows = list_session_objects(session_id)
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/objects")
@login_required
def create_object():
    payload = json_payload()
    row = create_session_object(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/objects/<int:object_id>")
@login_required
def update_object(object_id: int):
    try:
        row = get_session_object_or_error(object_id)
    except ValueError as exc:
        raise ApiError(code="not_found", message=str(exc), status_code=404) from exc
    payload = json_payload()
    row = update_session_object(row, payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.delete("/objects/<int:object_id>")
@login_required
def delete_object(object_id: int):
    try:
        row = get_session_object_or_error(object_id)
    except ValueError as exc:
        raise ApiError(code="not_found", message=str(exc), status_code=404) from exc
    summary = delete_session_object(row)
    return jsonify({"ok": True, "item": summary})


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
    get_session_or_404(session_id)

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


@api_bp.get("/lots/<int:lot_id>")
@login_required
def get_lot(lot_id: int):
    lot = get_lot_or_404(lot_id)
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
    summary = delete_lot_use_case(lot)
    db.session.commit()
    return jsonify({"ok": True, "item": summary})


@api_bp.post("/lots/<int:lot_id>/evaluate")
@login_required
def evaluate_one_lot(lot_id: int):
    payload = json_payload()
    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)

    mode = str(payload.get("mode", session.analysis_mode or "no_forecast"))
    strategy = payload.get("strategy")
    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = evaluate_session_lot(
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
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")
    if len(lot_ids) < 2:
        raise ValueError("lot_ids должен содержать минимум 2 лота")

    session = get_session_or_404(session_id)
    mode = str(payload.get("mode", session.analysis_mode or "no_forecast"))
    lots = db.session.query(Lot).filter(Lot.id.in_(lot_ids), Lot.session_id == session_id).all()
    if len(lots) < 2:
        raise ValueError("Лоты не найдены")

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = compare_session_lots(
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

    forecast, diag = parse_uploaded_forecast(
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
            "summary": summarize_stored_forecast(forecast),
        }
    )


@api_bp.get("/forecast/<int:forecast_id>")
@login_required
def get_forecast(forecast_id: int):
    forecast = db.session.get(Forecast, forecast_id)
    if forecast is None:
        raise ApiError(
            code="not_found",
            message=f"Прогноз {forecast_id} не найден",
            status_code=404,
        )
    return jsonify(
        {
            "ok": True,
            "item": {
                **forecast.to_dict(),
                "periods": [p.to_dict() for p in forecast.periods],
                "summary": summarize_stored_forecast(forecast),
            },
        }
    )


@api_bp.post("/forecast/<int:forecast_id>/analyze")
@login_required
def analyze_forecast(forecast_id: int):
    forecast = db.session.get(Forecast, forecast_id)
    if forecast is None:
        raise ApiError(
            code="not_found",
            message=f"Прогноз {forecast_id} не найден",
            status_code=404,
        )
    return jsonify({"ok": True, "item": summarize_stored_forecast(forecast)})


@api_bp.post("/recommend/best-lot")
@login_required
def recommend_best():
    payload = json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")

    session = get_session_or_404(session_id)
    mode = str(payload.get("mode", session.analysis_mode or "no_forecast"))
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    if not lots:
        raise ValueError("Нет лотов для рекомендации")

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = recommend_for_session(
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
    if lot_id <= 0:
        raise ValueError("lot_id обязателен")

    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)
    mode = str(payload.get("mode", session.analysis_mode or "no_forecast"))

    forecast = None
    if payload.get("forecast_id") is not None:
        forecast = db.session.get(Forecast, int(payload["forecast_id"]))

    out = strategy_fit_for_lot(
        session=session,
        lot=lot,
        mode=mode,
        forecast=forecast,
        corridor_override=payload.get("corridor_override"),
    )
    return jsonify({"ok": True, "item": out})
