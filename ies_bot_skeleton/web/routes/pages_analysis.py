from __future__ import annotations

import json
from typing import Any, Dict, List

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from ...application.admin import list_object_types_for_admin
from ...application.analysis import evaluate_session_lot, rank_session_lots
from ...application.objects import (
    create_session_object,
    delete_session_object,
    get_session_object_or_error,
    list_session_objects,
    update_session_object,
)
from ...application.portfolio import (
    buy_lot,
    reject_lot,
    restore_lot,
    undo_lot_purchase,
)
from ..extensions import db
from ..forms import (
    ConfirmDeleteForm,
    ConfirmLotDeleteForm,
    LotForm,
    LotPurchaseForm,
    LotRejectForm,
    LotRestoreForm,
    LotUndoPurchaseForm,
    ObjectInstanceForm,
)
from ..models import Forecast, GameSession, Lot, ObjectInstance, ObjectType
from ..services.evaluation import BUDGET_PRESERVATION_NOTE, ForecastCompatibilityError
from ..services.connection_advisor import (
    recommend_connection_for_profile,
    recommendations_for_session_objects,
)
from ..services.formatting import format_number, format_tick_range
from ..services.forecast_service import session_forecast_compatibility, summarize_forecast
from ..services.lots_dashboard import (
    analytics_by_lot_for_session,
    filter_lot_rows,
    lot_rows_for_session,
    lot_summary,
    sort_lot_rows,
)
from ..services.network import network_validation_summary
from ..services.object_instance_editor import parameter_rows, parameters_from_form
from ..services.stale import mark_stale_for_session
from ..services.strategy import build_strategy_snapshot
from ..services.test_game_preset import TEST_GAME_BUNDLED_FORECAST_NAME
from .api_support import lot_items_from_payload
from .page_support import (
    forecast_compatibility_guidance,
    nav,
    parse_json,
    session_analysis_view,
    session_shell_view,
    session_stale_ctx,
)
from .shared import pages_bp


def _missing_session_redirect(message: str = "Сессия не найдена."):
    flash(message, "error")
    return redirect(url_for("pages.dashboard"))


def _missing_lot_redirect(*, lot_id: int, session_id: int | None = None):
    flash(f"Лот {lot_id} не найден.", "error")
    if session_id is not None:
        return redirect(url_for("pages.lots_page", session_id=session_id))
    return redirect(url_for("pages.dashboard"))


def _missing_object_redirect(*, object_id: int, session_id: int | None = None):
    flash(f"Объект {object_id} не найден.", "error")
    if session_id is not None:
        return redirect(url_for("pages.system_view", session_id=session_id))
    return redirect(url_for("pages.dashboard"))


def _lot_items_payload(lot: Lot) -> List[Dict[str, Any]]:
    return [
        {
            "object_type_id": int(item.object_type_id),
            "quantity": int(item.quantity or 1),
            "overrides": dict(item.overrides_json or {}),
        }
        for item in lot.items
    ]


def _object_type_choices(*, current_type_id: int | None = None) -> List[tuple[int, str]]:
    choices = [
        (row.id, f"{row.name} ({row.code})")
        for row in list_object_types_for_admin(include_inactive=False)
        if not bool((row.rules_json or {}).get("hidden_from_ui"))
    ]
    if current_type_id is not None and current_type_id not in {row_id for row_id, _ in choices}:
        row = db.session.get(ObjectType, int(current_type_id))
        if row is not None:
            choices.append((row.id, f"{row.name} ({row.code})"))
    return choices


def _visible_object_types():
    return [
        row
        for row in list_object_types_for_admin(include_inactive=False)
        if not bool((row.rules_json or {}).get("hidden_from_ui"))
    ]


def _object_parent_choices(session: GameSession, *, current_object_id: int | None = None):
    choices = [(0, "Без родителя")]
    for row in session.objects:
        if current_object_id is not None and int(row.id) == int(current_object_id):
            continue
        label = row.custom_name or (row.object_type.name if row.object_type else f"Объект {row.id}")
        choices.append((row.id, f"{label} #{row.id}"))
    return choices


def _object_type_by_form_value(raw_value: Any) -> ObjectType | None:
    if raw_value in (None, "", 0, "0"):
        return None
    return db.session.get(ObjectType, int(raw_value))


def _forecast_line(session: GameSession) -> str:
    ctx = session_analysis_view(session)
    forecast = ctx["forecast_summary"]
    name = forecast.get("name") or TEST_GAME_BUNDLED_FORECAST_NAME
    tick_range = format_tick_range(forecast.get("tick_from"), forecast.get("tick_to"))
    return f"Анализ выполнен по прогнозу: {name}, такты {tick_range}."


def _refresh_after_portfolio_change(session: GameSession) -> None:
    try:
        rank_session_lots(session=session, lots=session.lots, persist=True)
        build_strategy_snapshot(session=session)
    except ForecastCompatibilityError:
        # Покупка/undo уже зафиксированы; если прогноз временно несовместим,
        # оставляем данные бюджета/портфеля и отдаём блокирующие подсказки в UI.
        return


def _blocked_evaluation_payload(*, compatibility_report: Dict[str, Any]) -> Dict[str, Any]:
    reason = (
        "; ".join(list(compatibility_report.get("blocking_reasons") or []))
        or "Прогноз несовместим."
    )

    def _scenario(label: str) -> Dict[str, Any]:
        scoped_reason = f"{label}: {reason}"
        return {
            "label": label,
            "revenue_total": 0.0,
            "cost_total": 0.0,
            "penalties_total": 0.0,
            "losses_total": 0.0,
            "net_profit": 0.0,
            "utility_score": 0.0,
            "bid_ceiling": 0.0,
            "recommended_bid": 0.0,
            "gross_profit_before_bid": 0.0,
            "net_profit_at_recommended_bid": 0.0,
            "net_profit_at_bid_ceiling": 0.0,
            "remaining_budget_after_recommended_bid": 0.0,
            "explanation": scoped_reason,
            "income_total": 0.0,
            "expenses_total": 0.0,
            "utility_total": 0.0,
            "expected_net_profit_at_current_price": 0.0,
            "comment": scoped_reason,
        }

    return {
        "summary_score": 0.0,
        "scenario_breakdown": {
            "worst": _scenario("Worst"),
            "base": _scenario("Base"),
            "best": _scenario("Best"),
        },
        "financial_breakdown": {
            "income": {"object_income": 0.0, "market_income": 0.0, "eco_value": 0.0, "total": 0.0},
            "expenses": {
                "entry_price": 0.0,
                "contract_costs": 0.0,
                "fuel_and_taxes": 0.0,
                "market_purchase": 0.0,
                "total": 0.0,
            },
            "losses_and_risks": {
                "network_losses": 0.0,
                "penalties": 0.0,
                "risk_total": 0.0,
                "flags": [],
                "total": 0.0,
            },
            "result": {
                "utility_total": 0.0,
                "net_profit": 0.0,
                "net_profit_at_current_price": 0.0,
                "gross_profit_before_bid": 0.0,
                "net_profit_at_recommended_bid": 0.0,
                "net_profit_at_max_bid": 0.0,
                "remaining_budget_after_recommended_bid": 0.0,
                "remaining_budget_after_max_bid": 0.0,
                "roi": 0.0,
                "payback_ratio": None,
                "threshold_bid": 0.0,
            },
            "ui_rows": [],
        },
        "decision_summary": {
            "recommended_bid_safe": 0.0,
            "recommended_bid_balanced": 0.0,
            "recommended_bid_aggressive": 0.0,
            "safe_bid": 0.0,
            "cautious_bid": 0.0,
            "target_bid": 0.0,
            "hard_cap": 0.0,
            "hard_ceiling_bid": 0.0,
            "budget_adjusted_bid": 0.0,
            "recommended_bid": 0.0,
            "max_bid": 0.0,
            "bid_formula": "strategic_anchor_repeatable_v4",
            "legacy_bid_formula": "deprecated_pwin_share_model",
            "bid_share": 0.0,
            "p_win": 0.0,
            "serious_competitors": 0,
            "gross_expected_profit_before_bid": 0.0,
            "recommended_bid_reason": reason,
            "max_bid_reason": reason,
            "bid_constraints_summary": reason,
            "budget_remaining": 0.0,
            "net_profit_at_recommended_bid": 0.0,
            "net_profit_at_max_bid": 0.0,
            "remaining_budget_after_recommended_bid": 0.0,
            "remaining_budget_after_max_bid": 0.0,
            "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
            "working_bid": 0.0,
            "working_bid_source": "zero",
            "working_bid_reason": reason,
            "portfolio_synergy": 0.0,
            "system_fit_score": 0.0,
        },
        "reasons": [reason],
        "risk_commentary": reason,
        "strategy_fit_text": "Оценка заблокирована до исправления совместимости прогноза.",
        "recommended_bid": 0.0,
        "max_bid": 0.0,
        "recommended_bid_reason": reason,
        "max_bid_reason": reason,
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "working_bid": 0.0,
        "working_bid_source": "zero",
        "working_bid_reason": reason,
        "is_stale": False,
        "stale_reason": "",
        "score_definition": "Оценка недоступна из-за несовместимого прогноза.",
        "metrics": {
            "scenarios": {},
            "decomposition": {},
            "bids": {
                "budget_adjusted_bid": 0.0,
                "recommended_bid": 0.0,
                "max_bid": 0.0,
                "bid_share": 0.0,
                "recommended_bid_safe": 0.0,
                "recommended_bid_balanced": 0.0,
                "recommended_bid_aggressive": 0.0,
                "p_win": 0.0,
                "serious_competitors": 0,
                "gross_expected_profit_before_bid": 0.0,
                "recommended_bid_reason": reason,
                "max_bid_reason": reason,
                "budget_remaining": 0.0,
                "net_profit_at_recommended_bid": 0.0,
                "net_profit_at_max_bid": 0.0,
                "remaining_budget_after_recommended_bid": 0.0,
                "remaining_budget_after_max_bid": 0.0,
                "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
                "risk_premium": 0.0,
                "reserve_margin": 0.0,
                "valuation_model": {
                    "model": "auction_bid_model",
                    "profile": "mixed",
                    "risk_band": "high",
                    "horizon_ticks": 0,
                    "p_worst": 0.0,
                    "p_base": 0.0,
                    "p_best": 0.0,
                    "p_exp": 0.0,
                    "scenario_volatility": 0.0,
                    "downside_gap": 0.0,
                    "risk_ratio": 1.0,
                    "risk_premium": 0.0,
                    "cap_share": 0.0,
                    "portfolio_synergy": 0.0,
                    "system_fit_score": 0.0,
                    "conservative_utility": {
                        "method": "weighted_expected_minus_volatility",
                        "value": 0.0,
                    },
                    "reserve_margin": 0.0,
                    "cautious_bid": 0.0,
                    "target_bid": 0.0,
                    "recommended_bid_safe": 0.0,
                    "recommended_bid_balanced": 0.0,
                    "recommended_bid_aggressive": 0.0,
                    "hard_ceiling_bid": 0.0,
                    "budget_adjusted_bid": 0.0,
                    "recommended_bid": 0.0,
                    "max_bid": 0.0,
                    "working_bid": 0.0,
                    "risk_adjusted_net_profit": 0.0,
                },
            },
            "portfolio_delta": {"net_profit_base": 0.0, "horizon_ticks": 0},
            "forecast_compatibility": compatibility_report,
            "role_breakdown": {},
            "synergy": {"score": 0.0},
            "system_check": {
                "status": "blocked",
                "message": reason,
                "items": [],
                "estimated_delta_total": 0.0,
                "blocked_items_count": 0,
                "feasible_items_count": 0,
                "avg_recommended_loss_pct": 0.0,
                "system_fit_score": 0.0,
                "recommended_points": [],
                "connection_block_reasons_count": 0,
            },
        },
        "system_check": {
            "status": "blocked",
            "message": reason,
            "items": [],
            "estimated_delta_total": 0.0,
            "blocked_items_count": 0,
            "feasible_items_count": 0,
            "avg_recommended_loss_pct": 0.0,
            "system_fit_score": 0.0,
            "recommended_points": [],
            "connection_block_reasons_count": 0,
        },
    }


@pages_bp.errorhandler(ForecastCompatibilityError)
def _pages_forecast_compatibility_error(exc: ForecastCompatibilityError):
    view_args = dict(request.view_args or {})
    lot_id = int(view_args.get("lot_id") or 0)
    session_id = int(view_args.get("session_id") or 0)
    lot = db.session.get(Lot, lot_id) if lot_id else None
    if lot is not None:
        session_id = int(lot.session_id)
    session = db.session.get(GameSession, session_id) if session_id else None
    guidance = forecast_compatibility_guidance(exc.report, session=session, lot=lot)
    flash(
        " ".join([guidance["primary_message"], *guidance["actions"]]).strip(),
        "error",
    )
    if lot is not None:
        return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))
    if session is not None:
        return redirect(url_for("pages.forecast_page", session_id=session.id))
    return redirect(url_for("pages.dashboard"))


def _render_object_editor(
    *, session: GameSession, form: ObjectInstanceForm, obj: ObjectInstance | None
):
    object_type = _object_type_by_form_value(form.object_type_id.data)
    current_parameters = obj.current_parameters_json if obj is not None else {}
    submitted_values = dict(request.form) if request.method == "POST" else None
    draft_parameters = dict(current_parameters or {})
    if object_type is not None and submitted_values is not None:
        try:
            draft_parameters = parameters_from_form(
                object_type,
                submitted_values,
                current_parameters=current_parameters,
            )
        except ValueError:
            draft_parameters = dict(current_parameters or {})
    draft_district = form.district.data or (obj.district if obj is not None else "default")
    connection_recommendation = None
    if object_type is not None:
        connection_recommendation = recommend_connection_for_profile(
            session=session,
            object_type=object_type,
            parameters=draft_parameters,
            district=draft_district,
            existing_objects=list(session.objects),
            exclude_object_id=obj.id if obj is not None else None,
        )
    title = "Редактирование объекта" if obj is not None else "Новый объект"
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Энергосистема", "pages.system_view", {"session_id": session.id}),
            (title, None, None),
        ],
        fallback_endpoint="pages.system_view",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.system_view", session_id=session.id),
    )
    return render_template(
        "analysis/object_edit.html",
        session=session,
        form=form,
        object_instance=obj,
        object_type=object_type,
        connection_recommendation=connection_recommendation,
        parameter_rows=parameter_rows(
            object_type,
            current_parameters=current_parameters,
            submitted_values=submitted_values,
        ),
        editor_mode="edit" if obj is not None else "create",
        show_analysis_context=False,
        **session_shell_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


def _render_lot_editor(*, session: GameSession, form: LotForm, object_types, lot: Lot | None):
    title = "Редактирование лота" if lot is not None else "Новый лот"
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            (title, None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.lots_page", session_id=session.id),
    )
    return render_template(
        "analysis/lot_edit.html",
        session=session,
        form=form,
        object_types=object_types,
        lot=lot,
        editor_mode="edit" if lot is not None else "create",
        show_analysis_context=False,
        forecast_line=_forecast_line(session),
        **session_shell_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/system/<int:session_id>")
@login_required
def system_view(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    object_rows = list_session_objects(session.id)
    network_summary = network_validation_summary(list(object_rows))
    connection_recommendations = recommendations_for_session_objects(session)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Энергосистема", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/system.html",
        session=session,
        network_summary=network_summary,
        issues=network_summary.issues,
        object_rows=object_rows,
        connection_recommendations=connection_recommendations,
        show_analysis_context=False,
        **session_shell_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.route("/system/<int:session_id>/objects/new", methods=["GET", "POST"])
@login_required
def object_create_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    form = ObjectInstanceForm()
    form.session_id.data = str(session.id)
    form.object_type_id.choices = _object_type_choices()
    form.parent_instance_id.choices = _object_parent_choices(session)
    if request.method == "GET" and form.object_type_id.choices:
        requested_type_id = request.args.get("object_type_id", type=int)
        available_type_ids = {row_id for row_id, _ in form.object_type_id.choices}
        form.object_type_id.data = (
            requested_type_id
            if requested_type_id in available_type_ids
            else form.object_type_id.choices[0][0]
        )
        form.is_active.data = True

    if form.validate_on_submit():
        try:
            object_type = _object_type_by_form_value(form.object_type_id.data)
            row = create_session_object(
                {
                    "session_id": session.id,
                    "object_type_id": form.object_type_id.data,
                    "custom_name": form.custom_name.data or "",
                    "district": form.district.data or "default",
                    "parent_instance_id": form.parent_instance_id.data or None,
                    "current_parameters": parameters_from_form(object_type, request.form),
                    "is_active": bool(form.is_active.data),
                }
            )
            flash(f"Объект «{row.custom_name or row.object_type.code}» создан", "success")
            return redirect(url_for("pages.system_view", session_id=session.id))
        except ValueError as exc:
            flash(str(exc), "error")
    elif request.method == "POST":
        for field_name, errors in form.errors.items():
            for error in errors:
                flash(f"{field_name}: {error}", "error")

    return _render_object_editor(session=session, form=form, obj=None)


@pages_bp.route("/system/objects/<int:object_id>/edit", methods=["GET", "POST"])
@login_required
def object_edit_page(object_id: int):
    try:
        obj = get_session_object_or_error(object_id)
    except ValueError:
        return _missing_object_redirect(object_id=object_id)

    session = obj.session
    if session is None:
        return _missing_session_redirect()

    form = ObjectInstanceForm()
    form.session_id.data = str(session.id)
    form.object_type_id.choices = _object_type_choices(current_type_id=obj.object_type_id)
    form.parent_instance_id.choices = _object_parent_choices(session, current_object_id=obj.id)

    if request.method == "GET":
        requested_type_id = request.args.get("object_type_id", type=int)
        available_type_ids = {row_id for row_id, _ in form.object_type_id.choices}
        form.object_type_id.data = (
            requested_type_id if requested_type_id in available_type_ids else obj.object_type_id
        )
        form.custom_name.data = obj.custom_name
        form.district.data = obj.district
        form.parent_instance_id.data = obj.parent_instance_id or 0
        form.is_active.data = bool(obj.is_active)

    if form.validate_on_submit():
        try:
            object_type = _object_type_by_form_value(form.object_type_id.data)
            update_session_object(
                obj,
                {
                    "object_type_id": form.object_type_id.data,
                    "custom_name": form.custom_name.data or "",
                    "district": form.district.data or "default",
                    "parent_instance_id": form.parent_instance_id.data or None,
                    "current_parameters": parameters_from_form(
                        object_type,
                        request.form,
                        current_parameters=obj.current_parameters_json,
                    ),
                    "is_active": bool(form.is_active.data),
                },
            )
            flash("Объект обновлён", "success")
            return redirect(url_for("pages.system_view", session_id=session.id))
        except ValueError as exc:
            flash(str(exc), "error")
    elif request.method == "POST":
        for field_name, errors in form.errors.items():
            for error in errors:
                flash(f"{field_name}: {error}", "error")

    return _render_object_editor(session=session, form=form, obj=obj)


@pages_bp.route("/system/objects/<int:object_id>/delete", methods=["GET", "POST"])
@login_required
def object_delete_confirm_page(object_id: int):
    try:
        obj = get_session_object_or_error(object_id)
    except ValueError:
        return _missing_object_redirect(object_id=object_id)

    session = obj.session
    if session is None:
        return _missing_session_redirect()

    form = ConfirmDeleteForm()
    if form.validate_on_submit():
        summary = delete_session_object(obj)
        label = summary.get("custom_name") or f"Объект {summary.get('object_id')}"
        flash(f"Объект «{label}» удалён", "success")
        return redirect(url_for("pages.system_view", session_id=session.id))

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Энергосистема", "pages.system_view", {"session_id": session.id}),
            ("Удаление объекта", None, None),
        ],
        fallback_endpoint="pages.system_view",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.system_view", session_id=session.id),
    )
    return render_template(
        "analysis/object_delete_confirm.html",
        session=session,
        object_instance=obj,
        form=form,
        summary={
            "children_count": obj.children.count(),
            "is_from_start_pack": bool(obj.is_from_start_pack),
            "source_lot_id": obj.source_lot_id,
        },
        show_analysis_context=False,
        **session_shell_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/lots/<int:session_id>")
@login_required
def lots_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    analysis_view = session_analysis_view(session)
    compatibility_report = dict(
        (analysis_view["forecast_summary"] or {}).get("compatibility_report") or {}
    )
    forecast_blocked = not bool(
        (analysis_view["forecast_summary"] or {}).get("is_compatible", True)
    )
    ranking_map: Dict[int, Dict[str, Any]] = {}
    if not forecast_blocked:
        try:
            ranking_map = analytics_by_lot_for_session(session)
        except ForecastCompatibilityError as exc:
            compatibility_report = dict(exc.report)
            forecast_blocked = True
    compatibility_guidance = forecast_compatibility_guidance(
        compatibility_report,
        session=session,
    )
    rows = lot_rows_for_session(session, ranking_map=ranking_map)
    rows = filter_lot_rows(rows, request.args)
    sort_key = str(request.args.get("sort", "bid_desc") or "bid_desc")
    rows = sort_lot_rows(rows, sort_key)
    shell_view = session_shell_view(session, analytics_by_lot=ranking_map)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/lots.html",
        session=session,
        lot_rows=rows,
        sort_key=sort_key,
        filters=request.args,
        forecast_line=_forecast_line(session),
        recalculate_url=url_for("api.recalculate_session_lots", session_id=session.id),
        forecast_blocked=forecast_blocked,
        forecast_compatibility_report=compatibility_report,
        compatibility_guidance=compatibility_guidance,
        show_analysis_context=False,
        **shell_view,
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/lots/item/<int:lot_id>")
@login_required
def lot_detail_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)

    session = lot.session
    if session is None:
        return _missing_session_redirect()

    analysis_view = session_analysis_view(session)
    compatibility_report = dict(
        (analysis_view["forecast_summary"] or {}).get("compatibility_report") or {}
    )
    forecast_blocked = not bool(
        (analysis_view["forecast_summary"] or {}).get("is_compatible", True)
    )
    if forecast_blocked:
        evaluation = _blocked_evaluation_payload(compatibility_report=compatibility_report)
    else:
        try:
            evaluation = evaluate_session_lot(session=session, lot=lot, persist=False)
        except ForecastCompatibilityError as exc:
            compatibility_report = dict(exc.report)
            forecast_blocked = True
            evaluation = _blocked_evaluation_payload(compatibility_report=compatibility_report)
    compatibility_guidance = forecast_compatibility_guidance(
        compatibility_report,
        session=session,
        lot=lot,
    )
    shell_view = session_shell_view(session)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            (lot.name, None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/lot_detail.html",
        session=session,
        lot=lot,
        lot_summary=lot_summary(lot),
        evaluation=evaluation,
        buy_form=LotPurchaseForm(),
        undo_form=LotUndoPurchaseForm(),
        reject_form=LotRejectForm(),
        restore_form=LotRestoreForm(),
        strategy_api_url=url_for("api.strategy_snapshot", session_id=session.id, top_n=20),
        forecast_blocked=forecast_blocked,
        forecast_compatibility_report=compatibility_report,
        compatibility_guidance=compatibility_guidance,
        forecast_line=_forecast_line(session),
        show_analysis_context=False,
        **shell_view,
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.route("/lots/item/<int:lot_id>/delete", methods=["GET", "POST"])
@login_required
def lot_delete_confirm_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)

    session = lot.session
    if session is None:
        return _missing_session_redirect()

    form = ConfirmLotDeleteForm()
    if form.validate_on_submit():
        from ...application.lots import delete_lot

        summary = delete_lot(lot)
        db.session.commit()
        mark_stale_for_session(session.id, reason="lot_changed")
        flash(f"Лот «{summary['name']}» удалён", "success")
        return redirect(url_for("pages.lots_page", session_id=session.id))

    summary = {
        "items_count": sum(max(1, int(item.quantity or 1)) for item in lot.items),
        "object_types_count": len(lot.items),
        "evaluations_count": len(lot.evaluations),
        "generated_objects_count": len(lot.generated_objects),
    }
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            ("Удаление лота", None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.lots_page", session_id=session.id),
    )
    return render_template(
        "analysis/lot_delete_confirm.html",
        session=session,
        lot=lot,
        form=form,
        summary=summary,
        show_analysis_context=False,
        **session_shell_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.route("/lots/item/<int:lot_id>/buy", methods=["GET", "POST"])
@login_required
def lot_buy_confirm_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    session = lot.session
    if session is None:
        return _missing_session_redirect()

    analysis_view = session_analysis_view(session)
    compatibility_report = dict(
        (analysis_view["forecast_summary"] or {}).get("compatibility_report") or {}
    )
    forecast_blocked = not bool(
        (analysis_view["forecast_summary"] or {}).get("is_compatible", True)
    )
    if forecast_blocked:
        evaluation = _blocked_evaluation_payload(compatibility_report=compatibility_report)
    else:
        try:
            evaluation = evaluate_session_lot(session=session, lot=lot, persist=False)
        except ForecastCompatibilityError as exc:
            compatibility_report = dict(exc.report)
            forecast_blocked = True
            evaluation = _blocked_evaluation_payload(compatibility_report=compatibility_report)
    compatibility_guidance = forecast_compatibility_guidance(
        compatibility_report,
        session=session,
        lot=lot,
    )
    shell_view = session_shell_view(session)
    form = LotPurchaseForm()
    if request.method == "GET":
        form.purchase_price.data = float(
            evaluation.get("recommended_bid")
            or (evaluation.get("decision_summary") or {}).get("recommended_bid")
            or evaluation.get("working_bid")
            or (evaluation.get("decision_summary") or {}).get("working_bid")
            or lot.current_bid
            or 0.0
        )
    if form.validate_on_submit():
        try:
            summary = buy_lot(session, lot, float(form.purchase_price.data or 0.0))
            db.session.commit()
            _refresh_after_portfolio_change(session)
            flash(
                f"Лот «{lot.name}» куплен по цене {format_number(summary['purchase_price'], 1)}",
                "success",
            )
            return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            ("Покупка лота", None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.lot_detail_page", lot_id=lot.id),
    )
    return render_template(
        "analysis/lot_buy_confirm.html",
        session=session,
        lot=lot,
        form=form,
        evaluation=evaluation,
        forecast_blocked=forecast_blocked,
        forecast_compatibility_report=compatibility_report,
        compatibility_guidance=compatibility_guidance,
        show_analysis_context=False,
        forecast_line=_forecast_line(session),
        **shell_view,
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.post("/lots/item/<int:lot_id>/undo-buy")
@login_required
def lot_undo_buy_action(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    session = lot.session
    if session is None:
        return _missing_session_redirect()
    form = LotUndoPurchaseForm()
    if form.validate_on_submit():
        try:
            summary = undo_lot_purchase(session, lot)
            db.session.commit()
            _refresh_after_portfolio_change(session)
            flash(
                "Покупка лота «{name}» отменена, бюджет восстановлен до {budget}".format(
                    name=lot.name,
                    budget=format_number(summary["remaining_budget"], 1),
                ),
                "success",
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))


@pages_bp.post("/lots/item/<int:lot_id>/reject")
@login_required
def lot_reject_action(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    form = LotRejectForm()
    if form.validate_on_submit():
        try:
            reject_lot(lot)
            db.session.commit()
            mark_stale_for_session(lot.session_id, reason="lot_changed")
            flash(f"Лот «{lot.name}» отклонён", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("pages.lots_page", session_id=lot.session_id))


@pages_bp.post("/lots/item/<int:lot_id>/restore")
@login_required
def lot_restore_action(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    form = LotRestoreForm()
    if form.validate_on_submit():
        try:
            restore_lot(lot)
            db.session.commit()
            mark_stale_for_session(lot.session_id, reason="lot_changed")
            flash(f"Лот «{lot.name}» снова доступен", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("pages.lots_page", session_id=lot.session_id))


@pages_bp.route("/lots/<int:session_id>/edit", methods=["GET", "POST"])
@login_required
def lots_edit(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    form = LotForm()
    form.session_id.data = session_id
    object_types = _visible_object_types()

    if form.validate_on_submit():
        lot = Lot(
            session_id=session_id,
            name=form.name.data,
            scope=form.scope.data,
            status="available",
            base_bid=float(form.base_bid.data or 0.0),
            current_bid=float(form.current_bid.data or 0.0),
            note=form.note.data or "",
            available_round=1,
        )
        db.session.add(lot)
        db.session.flush()

        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = parse_json(source, field_name="items", default=[])
            lot_items_from_payload(lot, items_payload)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _render_lot_editor(
                session=session, form=form, object_types=object_types, lot=None
            )

        db.session.add(lot)
        db.session.commit()
        mark_stale_for_session(session.id, reason="lot_changed")
        flash("Лот сохранён", "success")
        return redirect(url_for("pages.lots_page", session_id=session_id))
    elif request.method == "POST":
        for field_name, errors in form.errors.items():
            for error in errors:
                flash(f"{field_name}: {error}", "error")

    if not form.items_state_json.data:
        default_items: List[Dict[str, Any]] = []
        if object_types:
            default_items = [
                {"object_type_id": int(object_types[0].id), "quantity": 1, "overrides": {}}
            ]
        form.items_state_json.data = json.dumps(default_items, ensure_ascii=False)
        if not form.items_json.data:
            form.items_json.data = json.dumps(default_items, ensure_ascii=False, indent=2)

    return _render_lot_editor(session=session, form=form, object_types=object_types, lot=None)


@pages_bp.route("/lots/item/<int:lot_id>/edit", methods=["GET", "POST"])
@login_required
def lot_edit_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)

    session = lot.session
    if session is None:
        return _missing_session_redirect()

    form = LotForm()
    form.session_id.data = session.id
    object_types = _visible_object_types()

    if request.method == "GET":
        form.name.data = lot.name
        form.scope.data = lot.scope
        form.base_bid.data = lot.base_bid
        form.current_bid.data = lot.current_bid
        form.note.data = lot.note
        items_payload = _lot_items_payload(lot)
        form.items_state_json.data = json.dumps(items_payload, ensure_ascii=False)
        form.items_json.data = json.dumps(items_payload, ensure_ascii=False, indent=2)

    if form.validate_on_submit():
        lot.name = form.name.data
        lot.scope = form.scope.data
        lot.base_bid = float(form.base_bid.data or 0.0)
        lot.current_bid = float(form.current_bid.data or 0.0)
        lot.note = form.note.data or ""

        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = parse_json(source, field_name="items", default=[])
            lot_items_from_payload(
                lot,
                items_payload,
                preserve_existing_overrides=True,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _render_lot_editor(
                session=session, form=form, object_types=object_types, lot=lot
            )

        db.session.add(lot)
        db.session.commit()
        mark_stale_for_session(session.id, reason="lot_changed")
        flash("Лот обновлён", "success")
        return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))
    elif request.method == "POST":
        for field_name, errors in form.errors.items():
            for error in errors:
                flash(f"{field_name}: {error}", "error")

    return _render_lot_editor(session=session, form=form, object_types=object_types, lot=lot)


@pages_bp.get("/forecast/<int:session_id>")
@login_required
def forecast_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    forecasts = (
        db.session.query(Forecast)
        .filter_by(session_id=session_id)
        .order_by(Forecast.id.desc())
        .all()
    )
    analysis_ctx = session_shell_view(session)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Прогноз", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.session_page", session_id=session.id),
    )
    forecast_summaries = {forecast.id: summarize_forecast(forecast) for forecast in forecasts}
    compatibility_by_forecast = {
        forecast.id: session_forecast_compatibility(session=session, forecast=forecast)
        for forecast in forecasts
    }
    active_compatibility = session_forecast_compatibility(
        session=session,
        forecast=session.selected_forecast if session.selected_forecast_id else None,
    )
    compatibility_guidance = forecast_compatibility_guidance(
        dict(active_compatibility.get("compatibility_report") or {}),
        session=session,
    )

    return render_template(
        "analysis/forecast.html",
        session=session,
        forecasts=forecasts,
        active_forecast_id=int(session.selected_forecast_id or 0),
        active_forecast_summary=analysis_ctx["forecast_summary"],
        forecast_summaries=forecast_summaries,
        compatibility_by_forecast=compatibility_by_forecast,
        active_forecast_compatibility=active_compatibility,
        compatibility_guidance=compatibility_guidance,
        show_analysis_context=False,
        **analysis_ctx,
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/evaluation/<int:session_id>")
@login_required
def evaluation_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    flash("Раздел «История оценок» снят из основного UX. Используйте dashboard сессии.", "warning")
    return redirect(url_for("pages.session_page", session_id=session.id))


@pages_bp.get("/recommend/<int:session_id>")
@login_required
def recommend_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    flash("Раздел «Рекомендации» объединён с основной аналитикой лотов в dashboard.", "warning")
    return redirect(url_for("pages.session_page", session_id=session.id))


@pages_bp.get("/quick-auction/<int:session_id>")
@login_required
def quick_auction_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    analysis_view = session_analysis_view(session)
    compatibility_report = dict(
        (analysis_view["forecast_summary"] or {}).get("compatibility_report") or {}
    )
    forecast_blocked = not bool(
        (analysis_view["forecast_summary"] or {}).get("is_compatible", True)
    )
    ranking: List[Dict[str, Any]] = []
    ranking_map: Dict[int, Dict[str, Any]] = {}
    if not forecast_blocked:
        try:
            ranking_map = analytics_by_lot_for_session(session)
            available_rows = [
                row
                for row in lot_rows_for_session(session, ranking_map=ranking_map)
                if str(row.get("status") or "") == "available"
            ]
            available_rows = sort_lot_rows(available_rows, "utility_desc")
            ranking = []
            for row in available_rows:
                payload = dict(row.get("evaluation") or {})
                decision_summary = dict(payload.get("decision_summary") or {})
                recommended_safe = float(
                    row.get("recommended_bid_safe")
                    or decision_summary.get("recommended_bid_safe")
                    or decision_summary.get("cautious_bid")
                    or 0.0
                )
                recommended_balanced = float(
                    row.get("recommended_bid_balanced")
                    or decision_summary.get("recommended_bid_balanced")
                    or row.get("recommended_bid")
                    or row.get("working_bid")
                    or decision_summary.get("target_bid")
                    or 0.0
                )
                recommended_aggressive = float(
                    row.get("recommended_bid_aggressive")
                    or decision_summary.get("recommended_bid_aggressive")
                    or decision_summary.get("target_bid")
                    or recommended_balanced
                    or 0.0
                )
                decision_summary.update(
                    {
                        "target_bid": float(row.get("target_bid", recommended_balanced) or 0.0),
                        "budget_adjusted_bid": float(row.get("budget_adjusted_bid", 0.0) or 0.0),
                        "recommended_bid_safe": float(recommended_safe),
                        "recommended_bid_balanced": float(recommended_balanced),
                        "recommended_bid_aggressive": float(recommended_aggressive),
                        "recommended_bid": float(
                            row.get("recommended_bid", row.get("working_bid", recommended_balanced))
                            or 0.0
                        ),
                        "max_bid": float(row.get("max_bid", 0.0) or 0.0),
                        "bid_formula": str(
                            row.get("decision_summary", {}).get("bid_formula")
                            or payload.get("decision_summary", {}).get("bid_formula")
                            or "strategic_anchor_repeatable_v4"
                        ),
                        "bid_share": float(
                            row.get("decision_summary", {}).get("bid_share")
                            or payload.get("decision_summary", {}).get("bid_share")
                            or 0.0
                        ),
                        "gross_expected_profit_before_bid": float(
                            row.get("decision_summary", {}).get("gross_expected_profit_before_bid")
                            or payload.get("decision_summary", {}).get(
                                "gross_expected_profit_before_bid"
                            )
                            or 0.0
                        ),
                        "recommended_bid_reason": str(
                            row.get("recommended_bid_reason") or row.get("working_bid_reason") or ""
                        ),
                        "max_bid_reason": str(row.get("max_bid_reason") or ""),
                        "bid_constraints_summary": str(row.get("bid_constraints_summary") or ""),
                        "net_profit_at_recommended_bid": float(
                            row.get("net_profit_at_recommended_bid", 0.0) or 0.0
                        ),
                        "net_profit_at_max_bid": float(
                            row.get("net_profit_at_max_bid", 0.0) or 0.0
                        ),
                        "remaining_budget_after_recommended_bid": float(
                            row.get("remaining_budget_after_recommended_bid", 0.0) or 0.0
                        ),
                        "remaining_budget_after_max_bid": float(
                            row.get("remaining_budget_after_max_bid", 0.0) or 0.0
                        ),
                        "budget_preservation_note": str(
                            row.get("budget_preservation_note")
                            or payload.get("decision_summary", {}).get("budget_preservation_note")
                            or BUDGET_PRESERVATION_NOTE
                        ),
                        "working_bid": float(row.get("working_bid", 0.0) or 0.0),
                        "working_bid_source": str(row.get("working_bid_source") or "none"),
                        "working_bid_reason": str(row.get("working_bid_reason") or ""),
                    }
                )
                payload.update(
                    {
                        "lot_id": int(row.get("lot_id") or 0),
                        "name": str(row.get("name") or ""),
                        "structure": str(row.get("structure") or "Пустой лот"),
                        "structure_items": list(row.get("structure_items") or []),
                        "composition": str(row.get("composition") or "all"),
                        "composition_label": str(row.get("composition_label") or "—"),
                        "status": str(row.get("status") or ""),
                        "scope": str(getattr(row.get("lot"), "scope", "") or ""),
                        "price": float(row.get("price", 0.0) or 0.0),
                        "risk": float(row.get("risk", 0.0) or 0.0),
                        "net_profit": float(row.get("net_profit", 0.0) or 0.0),
                        "gross_profit_before_bid": float(
                            row.get("gross_profit_before_bid", 0.0) or 0.0
                        ),
                        "net_profit_at_recommended_bid": float(
                            row.get("net_profit_at_recommended_bid", 0.0) or 0.0
                        ),
                        "net_profit_at_max_bid": float(
                            row.get("net_profit_at_max_bid", 0.0) or 0.0
                        ),
                        "remaining_budget_after_recommended_bid": float(
                            row.get("remaining_budget_after_recommended_bid", 0.0) or 0.0
                        ),
                        "remaining_budget_after_max_bid": float(
                            row.get("remaining_budget_after_max_bid", 0.0) or 0.0
                        ),
                        "summary_score": float(
                            payload.get("summary_score", row.get("utility", 0.0)) or 0.0
                        ),
                        "working_bid": float(row.get("working_bid", 0.0) or 0.0),
                        "recommended_bid": float(
                            row.get("recommended_bid", row.get("working_bid", recommended_balanced))
                            or 0.0
                        ),
                        "recommended_bid_safe": float(recommended_safe),
                        "recommended_bid_balanced": float(recommended_balanced),
                        "recommended_bid_aggressive": float(recommended_aggressive),
                        "safe_bid": float(recommended_safe),
                        "hard_cap": float(
                            decision_summary.get(
                                "hard_cap",
                                decision_summary.get(
                                    "hard_ceiling_bid", row.get("hard_ceiling_bid", 0.0)
                                ),
                            )
                            or 0.0
                        ),
                        "max_bid": float(row.get("max_bid", 0.0) or 0.0),
                        "working_bid_source": str(row.get("working_bid_source") or "none"),
                        "working_bid_reason": str(row.get("working_bid_reason") or ""),
                        "working_bid_short_reason": str(row.get("working_bid_short_reason") or ""),
                        "decision_status": str(row.get("decision_status") or ""),
                        "decision_status_label": str(row.get("decision_status_label") or ""),
                        "decision_reason": str(row.get("decision_reason") or ""),
                        "decision_reason_short": str(row.get("decision_reason_short") or ""),
                        "bid_constraints_summary": str(row.get("bid_constraints_summary") or ""),
                        "budget_preservation_note": str(
                            row.get("budget_preservation_note") or BUDGET_PRESERVATION_NOTE
                        ),
                        "budget_adjusted_bid": float(row.get("budget_adjusted_bid", 0.0) or 0.0),
                        "target_bid": float(row.get("target_bid", recommended_balanced) or 0.0),
                        "connection_fit_status": str(row.get("connection_fit_status") or "neutral"),
                        "recommended_points": list(row.get("recommended_points") or []),
                        "bid_explainability": dict(
                            decision_summary.get("explainability")
                            or ((payload.get("metrics") or {}).get("bids") or {}).get(
                                "explainability"
                            )
                            or {}
                        ),
                        "decision_summary": decision_summary,
                    }
                )
                ranking.append(payload)
        except ForecastCompatibilityError as exc:
            compatibility_report = dict(exc.report)
            forecast_blocked = True
            ranking = []
    shortlist_lots = []
    for lot in session.lots:
        if str(lot.status or "") != "available":
            continue
        summary = lot_summary(lot)
        shortlist_lots.append(
            {
                "id": int(lot.id),
                "name": str(lot.name or f"Лот {lot.id}"),
                "current_bid": float(lot.current_bid or 0.0),
                "structure": str(summary.get("structure") or "Пустой лот"),
                "structure_items": list(summary.get("structure_items") or []),
                "composition_label": str(summary.get("composition_label") or "—"),
            }
        )
    compatibility_guidance = forecast_compatibility_guidance(
        compatibility_report,
        session=session,
    )
    shell_view = session_shell_view(session)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Быстрый аукцион", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/quick_auction.html",
        session=session,
        lots=shortlist_lots,
        ranking=ranking,
        forecast_blocked=forecast_blocked,
        forecast_compatibility_report=compatibility_report,
        compatibility_guidance=compatibility_guidance,
        forecast_line=_forecast_line(session),
        buy_form=LotPurchaseForm(),
        show_analysis_context=False,
        **shell_view,
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/strategy-fit/<int:lot_id>")
@login_required
def strategy_fit_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    flash(
        "Отдельная страница strategy fit больше не нужна: вся аналитика собрана в карточке лота.",
        "warning",
    )
    return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))
