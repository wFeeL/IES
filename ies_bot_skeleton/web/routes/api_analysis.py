from __future__ import annotations

import json

from flask import Response, current_app, jsonify, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from ...application.admin import apply_start_pack_to_session
from ...application.context import (
    session_analysis_settings_for_session,
    update_analysis_settings_for_session,
)
from ...application.analysis import evaluate_session_lot, rank_session_lots
from ...application.forecasts import parse_uploaded_forecast, summarize_stored_forecast
from ...application.lots import delete_lot as delete_lot_use_case
from ...application.objects import (
    create_session_object,
    delete_session_object,
    get_session_object_or_error,
    list_session_objects,
    update_session_object,
)
from ...application.portfolio import (
    buy_lot,
    portfolio_summary,
    reject_lot,
    restore_lot,
    undo_lot_purchase,
)
from ...application.recommendations import recommend_for_session, strategy_fit_for_lot
from ...application.sessions import create_session_record
from ..extensions import db
from ..models import Forecast, GameSession, Lot, ObjectType
from ..services.analysis_context import resolve_analysis_context
from ..services.evaluation import ForecastCompatibilityError
from ..services.forecast_service import session_forecast_compatibility
from ..services.session_io import (
    export_evaluations_csv,
    export_session_payload,
    import_session_payload,
)
from ..services.stale import mark_stale_for_session
from ..services.strategy import build_strategy_snapshot
from ..services.test_game_preset import TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME
from ..services.ui_text import working_bid_reason_short
from .api_support import (
    ApiError,
    api_error_response,
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


@api_bp.errorhandler(ApiError)
def _api_error(exc: ApiError):
    return api_error_response(exc)


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


@api_bp.errorhandler(ForecastCompatibilityError)
def _forecast_compatibility_error(exc: ForecastCompatibilityError):
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": "forecast_incompatible",
                    "message": str(exc),
                    "details": {"compatibility_report": dict(exc.report)},
                },
            }
        ),
        409,
    )


@api_bp.errorhandler(Exception)
def _unexpected_error(exc: Exception):
    current_app.logger.exception("Unhandled API exception: %s", exc)
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "Внутренняя ошибка API",
                    "details": {},
                },
            }
        ),
        500,
    )


def _session_recalculation_payload(
    session: GameSession,
    *,
    allow_partial: bool = False,
):
    try:
        rows = rank_session_lots(session=session, lots=session.lots, persist=True)
        strategy_snapshot = build_strategy_snapshot(session=session)
    except ForecastCompatibilityError as exc:
        if not allow_partial:
            raise
        portfolio = portfolio_summary(session)
        return {
            "items": [],
            "strategy": None,
            "meta": {
                "count": 0,
                "budget_total": float(portfolio["budget_total"]),
                "purchase_spent": float(portfolio["purchase_spent"]),
                "allpay_spent": float(portfolio["allpay_spent"]),
                "spent_total": float(portfolio["spent_total"]),
                "remaining_budget": float(portfolio["remaining_budget"]),
                "bought_lots_count": int(portfolio["bought_lots_count"]),
                "available_count": sum(
                    1 for lot in session.lots if str(lot.status or "") == "available"
                ),
                "non_zero_working_bid_count": 0,
                "shortlist_suggested_ids": [],
            },
            "error": {
                "code": "forecast_incompatible",
                "message": str(exc),
                "details": {"compatibility_report": dict(exc.report)},
            },
        }

    available_lot_ids = {int(lot.id) for lot in session.lots if str(lot.status or "") == "available"}
    available_rows = [row for row in rows if int(row.get("lot_id") or 0) in available_lot_ids]
    non_zero_working = [
        row
        for row in available_rows
        if float(row.get("working_bid") or (row.get("decision_summary") or {}).get("working_bid") or 0.0)
        > 0.0
    ]
    shortlist = sorted(
        non_zero_working,
        key=lambda row: (
            float(
                ((row.get("metrics") or {}).get("portfolio_delta") or {}).get(
                    "risk_adjusted_net_profit",
                    0.0,
                )
            ),
            float(row.get("working_bid") or 0.0),
        ),
        reverse=True,
    )
    portfolio = portfolio_summary(session)
    return {
        "items": rows,
        "strategy": strategy_snapshot,
        "meta": {
            "count": len(rows),
            "budget_total": float(portfolio["budget_total"]),
            "purchase_spent": float(portfolio["purchase_spent"]),
            "allpay_spent": float(portfolio["allpay_spent"]),
            "spent_total": float(portfolio["spent_total"]),
            "remaining_budget": float(portfolio["remaining_budget"]),
            "bought_lots_count": int(portfolio["bought_lots_count"]),
            "available_count": len(available_rows),
            "non_zero_working_bid_count": len(non_zero_working),
            "shortlist_suggested_ids": [int(row.get("lot_id") or 0) for row in shortlist[:8]],
        },
    }


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
    previous_forecast_id = int(row.selected_forecast_id or 0)
    updated = update_analysis_settings_for_session(row, payload)
    db.session.add(row)
    db.session.commit()
    if int(row.selected_forecast_id or 0) != previous_forecast_id:
        mark_stale_for_session(row.id, reason="forecast_changed")
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
    get_session_or_404(session_id)
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
        status="available",
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
    mark_stale_for_session(session_id, reason="lot_changed")
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
    for key in ("name", "scope", "note"):
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
    mark_stale_for_session(lot.session_id, reason="lot_changed")
    return jsonify({"ok": True, "item": lot.to_dict()})


@api_bp.delete("/lots/<int:lot_id>")
@login_required
def delete_lot(lot_id: int):
    lot = get_lot_or_404(lot_id)
    session_id = int(lot.session_id)
    summary = delete_lot_use_case(lot)
    db.session.commit()
    mark_stale_for_session(session_id, reason="lot_changed")
    return jsonify({"ok": True, "item": summary})


@api_bp.post("/lots/<int:lot_id>/evaluate")
@login_required
def evaluate_one_lot(lot_id: int):
    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)
    payload = json_payload()

    out = evaluate_session_lot(
        session=session,
        lot=lot,
        strategy=payload.get("strategy"),
        persist=True,
    )
    return jsonify({"ok": True, "item": out})


@api_bp.get("/sessions/<int:session_id>/lots/analytics")
@login_required
def lots_analytics(session_id: int):
    session = get_session_or_404(session_id)
    rows = rank_session_lots(session=session, lots=session.lots, persist=False)

    status_filter = str(request.args.get("status", "all") or "all")
    composition_filter = str(request.args.get("composition", "all") or "all")
    price_min = request.args.get("price_min", type=float)
    price_max = request.args.get("price_max", type=float)
    utility_min = request.args.get("utility_min", type=float)
    utility_max = request.args.get("utility_max", type=float)
    risk_max = request.args.get("risk_max", type=float)
    sort_key = str(request.args.get("sort", "utility_desc") or "utility_desc")

    enriched = []
    lots_by_id = {int(lot.id): lot for lot in session.lots}
    for item in rows:
        lot = lots_by_id.get(int(item["lot_id"]))
        if lot is None:
            continue
        decision_summary = dict(item.get("decision_summary") or {})
        system_check = dict(item.get("system_check") or {})
        recommended_bid = float(
            decision_summary.get(
                "recommended_bid",
                item.get("working_bid")
                or decision_summary.get("working_bid")
                or 0.0,
            )
            or 0.0
        )
        max_bid = float(
            decision_summary.get(
                "max_bid",
                min(
                    float(decision_summary.get("hard_ceiling_bid", 0.0) or 0.0),
                    float(decision_summary.get("budget_remaining", 0.0) or 0.0),
                ),
            )
            or 0.0
        )
        recommended_points = [
            str(point).strip()
            for point in list(system_check.get("recommended_points") or [])
            if str(point).strip()
        ]
        structure_items = []
        for it in lot.items:
            quantity = max(1, int(it.quantity or 1))
            object_name = it.object_type.name if it.object_type else f"Тип {it.object_type_id}"
            label = f"{object_name} ×{quantity}"
            structure_items.append(
                {
                    "object_type_id": int(it.object_type_id),
                    "code": (it.object_type.code if it.object_type else str(it.object_type_id)),
                    "name": object_name,
                    "quantity": quantity,
                    "label": label,
                }
            )
        structure = ", ".join(entry["label"] for entry in structure_items)
        composition = "all"
        categories = sorted(
            {
                (it.object_type.category if it.object_type else "")
                for it in lot.items
                if it.object_type
            }
        )
        if "consumer" in categories and "generator" in categories:
            composition = "mixed"
        elif "generator" in categories:
            composition = "generator"
        elif "consumer" in categories:
            composition = "consumer"
        elif "storage" in categories:
            composition = "storage"
        elif "infrastructure" in categories:
            composition = "infrastructure"
        enriched.append(
            {
                **item,
                "status": lot.status,
                "structure": structure or "Пустой лот",
                "structure_items": structure_items,
                "composition": composition,
                "price": float(
                    lot.purchase_price
                    if lot.status == "bought" and lot.purchase_price is not None
                    else lot.current_bid or 0.0
                ),
                "risk": float(
                    (item.get("financial_breakdown") or {})
                    .get("losses_and_risks", {})
                    .get("risk_total", 0.0)
                    or 0.0
                ),
                "net_profit": float(
                    (item.get("financial_breakdown") or {}).get("result", {}).get("net_profit", 0.0)
                    or 0.0
                ),
                "gross_profit_before_bid": float(
                    decision_summary.get(
                        "gross_expected_profit_before_bid",
                        (item.get("financial_breakdown") or {})
                        .get("result", {})
                        .get("gross_profit_before_bid", 0.0),
                    )
                    or 0.0
                ),
                "net_profit_at_recommended_bid": float(
                    decision_summary.get(
                        "net_profit_at_recommended_bid",
                        (item.get("financial_breakdown") or {})
                        .get("result", {})
                        .get("net_profit_at_recommended_bid", 0.0),
                    )
                    or 0.0
                ),
                "net_profit_at_max_bid": float(
                    decision_summary.get(
                        "net_profit_at_max_bid",
                        (item.get("financial_breakdown") or {})
                        .get("result", {})
                        .get("net_profit_at_max_bid", 0.0),
                    )
                    or 0.0
                ),
                "remaining_budget_after_recommended_bid": float(
                    decision_summary.get(
                        "remaining_budget_after_recommended_bid",
                        (item.get("financial_breakdown") or {})
                        .get("result", {})
                        .get("remaining_budget_after_recommended_bid", 0.0),
                    )
                    or 0.0
                ),
                "remaining_budget_after_max_bid": float(
                    decision_summary.get(
                        "remaining_budget_after_max_bid",
                        (item.get("financial_breakdown") or {})
                        .get("result", {})
                        .get("remaining_budget_after_max_bid", 0.0),
                    )
                    or 0.0
                ),
                "cautious_bid": float(
                    (item.get("decision_summary") or {}).get("cautious_bid", 0.0) or 0.0
                ),
                "target_bid": float(
                    decision_summary.get("target_bid", 0.0) or 0.0
                ),
                "hard_ceiling_bid": float(
                    decision_summary.get("hard_ceiling_bid", 0.0) or 0.0
                ),
                "budget_adjusted_bid": float(
                    decision_summary.get("budget_adjusted_bid", 0.0) or 0.0
                ),
                "recommended_bid": float(recommended_bid),
                "max_bid": float(max_bid),
                "recommended_bid_reason": str(
                    decision_summary.get("recommended_bid_reason")
                    or decision_summary.get("working_bid_reason")
                    or item.get("working_bid_reason")
                    or ""
                ),
                "budget_preservation_note": str(
                    decision_summary.get("budget_preservation_note") or ""
                ),
                "max_bid_reason": str(decision_summary.get("max_bid_reason") or ""),
                "connection_fit_status": str(system_check.get("status") or "neutral"),
                "recommended_points": recommended_points,
                "connection_block_reasons_count": int(
                    system_check.get("connection_block_reasons_count", 0) or 0
                ),
                "working_bid": float(
                    item.get("working_bid")
                    or decision_summary.get("working_bid")
                    or 0.0
                ),
                "working_bid_source": str(
                    item.get("working_bid_source")
                    or decision_summary.get("working_bid_source")
                    or "zero"
                ),
                "working_bid_reason": str(
                    item.get("working_bid_reason")
                    or decision_summary.get("working_bid_reason")
                    or ""
                ),
                "working_bid_short_reason": working_bid_reason_short(
                    item.get("working_bid_reason")
                    or decision_summary.get("working_bid_reason")
                    or ""
                ),
                "system_check": system_check,
            }
        )

    if status_filter != "all":
        enriched = [row for row in enriched if row["status"] == status_filter]
    if composition_filter != "all":
        enriched = [row for row in enriched if row["composition"] == composition_filter]
    if price_min is not None:
        enriched = [row for row in enriched if row["price"] >= price_min]
    if price_max is not None:
        enriched = [row for row in enriched if row["price"] <= price_max]
    if utility_min is not None:
        enriched = [row for row in enriched if float(row.get("summary_score", 0.0)) >= utility_min]
    if utility_max is not None:
        enriched = [row for row in enriched if float(row.get("summary_score", 0.0)) <= utility_max]
    if risk_max is not None:
        enriched = [row for row in enriched if row["risk"] <= risk_max]

    if sort_key == "profit_desc":
        enriched.sort(key=lambda row: row["net_profit"], reverse=True)
    elif sort_key == "risk_asc":
        enriched.sort(key=lambda row: row["risk"])
    elif sort_key == "bid_desc":
        enriched.sort(
            key=lambda row: float(
                row.get("recommended_bid") or row.get("working_bid") or row.get("target_bid") or 0.0
            ),
            reverse=True,
        )
    elif sort_key == "price_asc":
        enriched.sort(key=lambda row: row["price"])
    elif sort_key == "price_desc":
        enriched.sort(key=lambda row: row["price"], reverse=True)
    else:
        enriched.sort(key=lambda row: float(row.get("summary_score", 0.0)), reverse=True)

    return jsonify({"ok": True, "items": enriched})


@api_bp.post("/sessions/<int:session_id>/recalculate")
@login_required
def recalculate_session_lots(session_id: int):
    session = get_session_or_404(session_id)
    payload = _session_recalculation_payload(session)
    return jsonify(
        {
            "ok": True,
            "items": payload["items"],
            "strategy": payload["strategy"],
            "meta": payload["meta"],
        }
    )


@api_bp.get("/sessions/<int:session_id>/strategy")
@login_required
def strategy_snapshot(session_id: int):
    session = get_session_or_404(session_id)
    strategy = (request.args.get("strategy") or "").strip() or None
    top_n = max(1, min(20, int(request.args.get("top_n", 5) or 5)))
    beam_width = max(2, min(20, int(request.args.get("beam_width", 7) or 7)))
    max_group_size = max(3, min(7, int(request.args.get("max_group_size", 5) or 5)))
    forecast_id_raw = request.args.get("forecast_id", type=int)
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast_id_raw if forecast_id_raw else None
    )
    out = build_strategy_snapshot(
        session=session,
        strategy=strategy,
        forecast=analysis_ctx["forecast"],
        top_n=top_n,
        beam_width=beam_width,
        max_group_size=max_group_size,
    )
    return jsonify({"ok": True, "item": out})


@api_bp.get("/sessions/<int:session_id>/forecast-compatibility")
@login_required
def session_forecast_compatibility_endpoint(session_id: int):
    session = get_session_or_404(session_id)
    forecast_id_raw = request.args.get("forecast_id", type=int)
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast_id_raw if forecast_id_raw else None
    )
    out = session_forecast_compatibility(session=session, forecast=analysis_ctx["forecast"])
    return jsonify({"ok": True, "item": out})


@api_bp.post("/forecast/upload")
@login_required
def upload_forecast():
    session_id = request.form.get("session_id", type=int)
    if not session_id:
        raise ValueError("session_id обязателен")
    session = get_session_or_404(session_id)
    name = (request.form.get("name") or TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME).strip()
    name = name or TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME

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
        mark_stale_for_session(session.id, reason="forecast_changed")

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


@api_bp.post("/lots/<int:lot_id>/buy")
@login_required
def buy_lot_endpoint(lot_id: int):
    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)
    payload = json_payload()
    purchase_price_raw = payload.get("purchase_price")
    if purchase_price_raw in (None, ""):
        evaluation = evaluate_session_lot(session=session, lot=lot, persist=False)
        purchase_price = float(
            evaluation.get("recommended_bid")
            or (evaluation.get("decision_summary") or {}).get("recommended_bid")
            or evaluation.get("working_bid")
            or (evaluation.get("decision_summary") or {}).get("working_bid")
            or 0.0
        )
    else:
        purchase_price = float(purchase_price_raw or 0.0)
    summary = buy_lot(session, lot, purchase_price)
    db.session.commit()
    refresh = _session_recalculation_payload(session, allow_partial=True)
    return jsonify({"ok": True, "item": summary, "refresh": refresh})


@api_bp.post("/lots/<int:lot_id>/undo-buy")
@login_required
def undo_buy_lot_endpoint(lot_id: int):
    lot = get_lot_or_404(lot_id)
    session = get_session_or_404(lot.session_id)
    summary = undo_lot_purchase(session, lot)
    db.session.commit()
    refresh = _session_recalculation_payload(session, allow_partial=True)
    return jsonify({"ok": True, "item": summary, "refresh": refresh})


@api_bp.post("/lots/<int:lot_id>/reject")
@login_required
def reject_lot_endpoint(lot_id: int):
    lot = get_lot_or_404(lot_id)
    summary = reject_lot(lot)
    db.session.commit()
    mark_stale_for_session(lot.session_id, reason="lot_changed")
    return jsonify({"ok": True, "item": summary})


@api_bp.post("/lots/<int:lot_id>/restore")
@login_required
def restore_lot_endpoint(lot_id: int):
    lot = get_lot_or_404(lot_id)
    summary = restore_lot(lot)
    db.session.commit()
    mark_stale_for_session(lot.session_id, reason="lot_changed")
    return jsonify({"ok": True, "item": summary})


@api_bp.post("/recommend/best-lot")
@login_required
def recommend_best():
    payload = json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    strategy = payload.get("strategy")

    if session_id <= 0:
        raise ValueError("session_id обязателен")

    session = get_session_or_404(session_id)
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    if not lots:
        raise ValueError("Нет лотов для рекомендации")

    out = recommend_for_session(
        session=session,
        lots=lots,
        strategy=strategy,
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

    out = strategy_fit_for_lot(
        session=session,
        lot=lot,
    )
    return jsonify({"ok": True, "item": out})
