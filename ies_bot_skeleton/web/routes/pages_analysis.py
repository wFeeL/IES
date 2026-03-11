from __future__ import annotations

import json
from typing import Any, Dict, List

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..extensions import db
from ..forms import LotForm
from ..models import EvaluationResult, Forecast, GameSession, Lot
from ..services.analysis_context import resolve_analysis_context
from ..services.evaluation import compare_lots, recommend_best_lot, strategy_fit
from ..services.forecast_service import summarize_forecast
from ..services.network import validate_session_network
from ..services.object_type_admin import list_object_types
from .page_support import nav, parse_json, session_analysis_view, session_stale_ctx
from .shared import pages_bp


def _mode_kwargs(session: GameSession) -> Dict[str, Any]:
    analysis_ctx = resolve_analysis_context(session)
    return {
        "mode": analysis_ctx["mode"],
        "forecast": analysis_ctx["forecast"],
        "corridor_override": analysis_ctx["corridor_settings"],
        "analysis_ctx": analysis_ctx,
    }


def _lot_summary(lot: Lot) -> Dict[str, Any]:
    counts = {"consumer": 0, "generator": 0, "storage": 0, "infrastructure": 0}
    items_total = 0
    for item in lot.items:
        qty = max(1, int(item.quantity or 1))
        items_total += qty
        category = (item.object_type.category if item.object_type else "other") or "other"
        counts[category] = counts.get(category, 0) + qty
    compatibility = "Требуется ручная проверка сети"
    if counts.get("infrastructure", 0) > 0:
        compatibility = "Есть инфраструктурные объекты, состав ближе к сетевому сценарию"
    elif counts.get("generator", 0) > 0 and counts.get("consumer", 0) > 0:
        compatibility = "Смешанный лот: генерация и потребление"
    elif counts.get("generator", 0) > 0:
        compatibility = "Преимущественно генерация"
    elif counts.get("consumer", 0) > 0:
        compatibility = "Преимущественно потребление"
    return {
        "items_total": items_total,
        "counts": counts,
        "compatibility": compatibility,
    }


@pages_bp.get("/system/<int:session_id>")
@login_required
def system_view(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    issues = validate_session_network(list(session.objects))
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
        issues=issues,
        show_analysis_context=False,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/lots/<int:session_id>")
@login_required
def lots_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).order_by(Lot.id).all()
    analysis_kwargs = _mode_kwargs(session)
    latest_eval_by_lot: Dict[int, Dict[str, Any]] = {}
    for lot in lots:
        latest_eval_by_lot[lot.id] = compare_lots(
            session=session,
            lots=[lot],
            mode=analysis_kwargs["mode"],
            forecast=analysis_kwargs["forecast"],
            corridor_override=analysis_kwargs["corridor_override"],
        )[0]
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
        lots=lots,
        lot_summaries={lot.id: _lot_summary(lot) for lot in lots},
        latest_eval_by_lot=latest_eval_by_lot,
        **analysis_kwargs,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.route("/lots/<int:session_id>/edit", methods=["GET", "POST"])
@login_required
def lots_edit(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    form = LotForm()
    form.session_id.data = session_id
    object_types = list_object_types(include_inactive=False)

    submitted = form.validate_on_submit()
    if submitted:
        lot = Lot(
            session_id=session_id,
            name=form.name.data,
            scope=form.scope.data,
            status=form.status.data,
            base_bid=float(form.base_bid.data or 0.0),
            current_bid=float(form.current_bid.data or 0.0),
            note=form.note.data or "",
            available_round=int(form.available_round.data or 1),
        )
        db.session.add(lot)
        db.session.flush()

        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = parse_json(source, field_name="items", default=[])
            from .api_support import lot_items_from_payload

            lot_items_from_payload(lot, items_payload)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            ctx = nav(
                breadcrumb_items=[
                    ("Сессии", "pages.dashboard", None),
                    (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
                    ("Лоты", "pages.lots_page", {"session_id": session.id}),
                    ("Новый лот", None, None),
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
                show_analysis_context=False,
                **session_analysis_view(session),
                **ctx,
                **session_stale_ctx(session),
            )

        db.session.add(lot)
        db.session.commit()
        flash("Лот сохранен", "success")
        return redirect(url_for("pages.lots_page", session_id=session_id))
    elif request.method == "POST":
        for field_name, errors in form.errors.items():
            for error in errors:
                flash(f"{field_name}: {error}", "error")

    if not form.items_state_json.data:
        default_items: List[Dict[str, Any]] = []
        if object_types:
            default_items = [
                {
                    "object_type_id": int(object_types[0].id),
                    "quantity": 1,
                    "overrides": {},
                }
            ]
        form.items_state_json.data = json.dumps(default_items, ensure_ascii=False)
        if not form.items_json.data:
            form.items_json.data = json.dumps(default_items, ensure_ascii=False, indent=2)

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            ("Новый лот", None, None),
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
        show_analysis_context=False,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/forecast/<int:session_id>")
@login_required
def forecast_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    forecasts = db.session.query(Forecast).filter_by(session_id=session_id).order_by(Forecast.id.desc()).all()
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Прогнозы", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.session_page", session_id=session.id),
    )
    return render_template(
        "analysis/forecast.html",
        session=session,
        forecasts=forecasts,
        forecast_summaries={forecast.id: summarize_forecast(forecast) for forecast in forecasts},
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/evaluation/<int:session_id>")
@login_required
def evaluation_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    evaluations = (
        db.session.query(EvaluationResult)
        .filter_by(session_id=session_id)
        .order_by(EvaluationResult.created_at.desc())
        .limit(100)
        .all()
    )
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("История оценок", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/evaluation.html",
        session=session,
        evaluations=evaluations,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/compare/<int:session_id>")
@login_required
def compare_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    analysis_kwargs = _mode_kwargs(session)
    ranking = (
        compare_lots(
            session=session,
            lots=lots,
            mode=analysis_kwargs["mode"],
            forecast=analysis_kwargs["forecast"],
            corridor_override=analysis_kwargs["corridor_override"],
        )
        if lots
        else []
    )
    top_pair = ranking[:2]
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Сравнение", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/compare.html",
        session=session,
        lots=lots,
        ranking=ranking,
        top_pair=top_pair,
        **analysis_kwargs,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/recommend/<int:session_id>")
@login_required
def recommend_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    analysis_kwargs = _mode_kwargs(session)
    recommendation = (
        recommend_best_lot(
            session=session,
            lots=lots,
            mode=analysis_kwargs["mode"],
            forecast=analysis_kwargs["forecast"],
            corridor_override=analysis_kwargs["corridor_override"],
        )
        if lots
        else None
    )
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Рекомендации", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/recommend.html",
        session=session,
        lots=lots,
        recommendation=recommendation,
        **analysis_kwargs,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/quick-auction/<int:session_id>")
@login_required
def quick_auction_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    analysis_kwargs = _mode_kwargs(session)
    ranking = (
        compare_lots(
            session=session,
            lots=lots,
            mode=analysis_kwargs["mode"],
            forecast=analysis_kwargs["forecast"],
            corridor_override=analysis_kwargs["corridor_override"],
        )
        if lots
        else []
    )
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
        lots=lots,
        ranking=ranking,
        **analysis_kwargs,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/strategy-fit/<int:lot_id>")
@login_required
def strategy_fit_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return redirect(url_for("pages.dashboard"))
    session = db.session.get(GameSession, lot.session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    analysis_kwargs = _mode_kwargs(session)
    fit = strategy_fit(
        session=session,
        lot=lot,
        mode=analysis_kwargs["mode"],
        forecast=analysis_kwargs["forecast"],
        corridor_override=analysis_kwargs["corridor_override"],
    )
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            (f"Стратегии / Лот {lot.id}", None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "analysis/strategy_fit.html",
        session=session,
        lot=lot,
        fit=fit,
        **analysis_kwargs,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )
