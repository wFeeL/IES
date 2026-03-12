from __future__ import annotations

from typing import Dict, List

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ...application.analysis import rank_session_lots
from ...application.context import resolve_session_analysis_context, update_analysis_settings_for_session
from ...application.portfolio import portfolio_rows, portfolio_summary
from ...application.sessions import create_session_record, delete_session_record
from ..extensions import db
from ..forms import ConfirmDeleteForm, ForecastSelectionForm, LoginForm, SessionForm, SessionImportForm
from ..models import GameSession, ObjectType, Ruleset, User
from ..services.navigation import is_safe_internal_url, safe_next_url
from ..services.session_io import import_session_payload
from ..services.stale import mark_stale_for_session
from ..services.strategy_catalog import strategy_list, strategy_meta
from ..services.ui_text import SESSION_TERMS, strategy_label
from .page_support import nav, parse_json, session_analysis_view, session_stale_ctx
from .shared import pages_bp


def _fmt_datetime(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _missing_session_redirect():
    flash("Сессия не найдена.", "error")
    return redirect(url_for("pages.dashboard"))


def _dashboard_cards(sessions: List[GameSession]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for session in sessions:
        analysis_ctx = resolve_session_analysis_context(session)
        portfolio = portfolio_summary(session)
        out.append(
            {
                "id": session.id,
                "title": session.title,
                "strategy_label": strategy_label(session.selected_strategy),
                "budget_total": float(session.budget_total or 0.0),
                "remaining_budget": float(portfolio["remaining_budget"]),
                "lots_count": len(session.lots),
                "objects_count": len(session.objects),
                "bought_lots_count": int(portfolio["bought_lots_count"]),
                "forecast_name": analysis_ctx["forecast_context"]["forecast_name"],
                "forecast_source": analysis_ctx["forecast_context"]["source_label"],
                "updated_at_label": _fmt_datetime(session.updated_at),
                "url": url_for("pages.session_page", session_id=session.id),
                "delete_url": url_for("pages.session_delete_confirm_page", session_id=session.id),
            }
        )
    return out


def _workbench_lot_summary(session: GameSession, ranking: List[Dict[str, object]]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    lots_by_id = {int(lot.id): lot for lot in session.lots}
    for item in ranking:
        lot = lots_by_id.get(int(item["lot_id"]))
        if lot is None or lot.status != "available":
            continue
        names = []
        for lot_item in lot.items[:4]:
            code = lot_item.object_type.code if lot_item.object_type else lot_item.object_type_id
            names.append(f"{code} ×{max(1, int(lot_item.quantity or 1))}")
        financial = dict(item.get("financial_breakdown") or {})
        result = dict(financial.get("result") or {})
        losses = dict(financial.get("losses_and_risks") or {})
        rows.append(
            {
                "lot_id": int(lot.id),
                "name": lot.name,
                "structure": ", ".join(names) if names else "Пустой лот",
                "utility": float(item.get("summary_score", 0.0) or 0.0),
                "net_profit": float(result.get("net_profit", 0.0) or 0.0),
                "risk": float(losses.get("risk_total", 0.0) or 0.0),
                "max_bid": float((item.get("decision_summary") or {}).get("hard_bid", 0.0) or 0.0),
                "status": lot.status,
                "is_stale": bool(item.get("is_stale")),
            }
        )
    return rows


@pages_bp.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    return redirect(url_for("pages.login"))


@pages_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))

    form = LoginForm()
    next_target = (request.values.get("next") or "").strip()
    if next_target and not is_safe_internal_url(next_target):
        next_target = ""

    if form.validate_on_submit():
        user = db.session.query(User).filter_by(username=form.username.data).one_or_none()
        if user is None or not user.check_password(form.password.data):
            flash("Неверный логин или пароль", "error")
        else:
            login_user(user, remember=bool(form.remember.data))
            return redirect(safe_next_url(fallback_endpoint="pages.dashboard"))

    return render_template("core/login.html", form=form, next_target=next_target)


@pages_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("pages.login"))


@pages_bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    form = SessionForm()
    import_form = SessionImportForm()
    rulesets = db.session.query(Ruleset).filter_by(is_active=True).all()
    default_budget = 200.0
    if rulesets:
        form.ruleset_id.choices = [
            (row.id, f"{row.name} ({row.code}:{row.version})") for row in rulesets
        ]
        if not form.ruleset_id.data:
            form.ruleset_id.data = rulesets[0].id
        cfg = dict(rulesets[0].config_json or {})
        default_budget = float((cfg.get("auction", {}) or {}).get("starting_budget", 200.0) or 200.0)
    else:
        form.ruleset_id.choices = []
    if request.method == "GET" and not form.budget_total.data:
        form.budget_total.data = default_budget

    if form.validate_on_submit():
        row = create_session_record(
            {
                "title": form.title.data,
                "ruleset_id": form.ruleset_id.data,
                "selected_strategy": form.selected_strategy.data,
                "budget_total": float(form.budget_total.data or default_budget),
                "allpay_spent": 0.0,
            }
        )
        return redirect(url_for("pages.session_page", session_id=row.id))

    sessions = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return render_template(
        "core/dashboard.html",
        sessions=sessions,
        session_cards=_dashboard_cards(sessions),
        form=form,
        import_form=import_form,
        session_terms=SESSION_TERMS,
        selected_strategy_meta=strategy_meta(form.selected_strategy.data or "balanced"),
        strategy_catalog=strategy_list(),
    )


@pages_bp.get("/sessions/<int:session_id>")
@login_required
def session_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    analysis_ctx = resolve_session_analysis_context(session)
    forecast_form = ForecastSelectionForm()
    forecast_form.selected_forecast_id.choices = [(0, "Встроенный базовый прогноз")] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})")
        for forecast in session.forecasts
    ]
    forecast_form.selected_forecast_id.data = int(session.selected_forecast_id or 0)

    ranking = rank_session_lots(session=session, lots=session.lots, persist=False) if session.lots else []
    analytics_by_lot = {int(row["lot_id"]): row for row in ranking}
    portfolio = portfolio_summary(session, analytics_by_lot=analytics_by_lot)
    purchased_rows = portfolio_rows(session, analytics_by_lot=analytics_by_lot)
    available_rows = _workbench_lot_summary(session, ranking)
    readiness = {
        "objects_count": len(session.objects),
        "lots_count": len(session.lots),
        "forecasts_count": len(session.forecasts),
        "active_forecast_name": analysis_ctx["forecast_context"]["forecast_name"],
        "uses_bundled_forecast": analysis_ctx["forecast_context"]["source"] == "bundled_forecast",
    }
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template(
        "core/workbench.html",
        session=session,
        readiness=readiness,
        forecast_form=forecast_form,
        forecast_card=analysis_ctx["forecast_summary"],
        portfolio=portfolio,
        available_rows=available_rows[:8],
        purchased_rows=purchased_rows,
        delete_url=url_for("pages.session_delete_confirm_page", session_id=session.id),
        export_json_url=url_for("api.export_session", session_id=session.id),
        export_csv_url=url_for("api.export_evaluations", session_id=session.id),
        recalculate_url=url_for("api.recalculate_session_lots", session_id=session.id),
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.route("/sessions/<int:session_id>/delete", methods=["GET", "POST"])
@login_required
def session_delete_confirm_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    form = ConfirmDeleteForm()
    if form.validate_on_submit():
        title = delete_session_record(session)
        flash(f"Сессия «{title}» удалена", "success")
        return redirect(url_for("pages.dashboard"))

    summary = {
        "objects_count": len(session.objects),
        "lots_count": len(session.lots),
        "forecasts_count": len(session.forecasts),
        "evaluations_count": len(session.evaluations),
    }
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (session.title, "pages.session_page", {"session_id": session.id}),
            ("Удаление сессии", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.session_page", session_id=session.id),
    )
    return render_template(
        "core/session_delete_confirm.html",
        session=session,
        form=form,
        summary=summary,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.post("/sessions/<int:session_id>/forecast-selection")
@login_required
def session_forecast_selection_action(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    form = ForecastSelectionForm()
    form.selected_forecast_id.choices = [(0, "Встроенный базовый прогноз")] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})")
        for forecast in session.forecasts
    ]
    previous_forecast_id = int(session.selected_forecast_id or 0)
    if form.validate_on_submit():
        update_analysis_settings_for_session(session, {"selected_forecast_id": form.selected_forecast_id.data or None})
        db.session.add(session)
        db.session.commit()
        if int(session.selected_forecast_id or 0) != previous_forecast_id:
            mark_stale_for_session(session.id, reason="forecast_changed")
        flash("Активный прогноз обновлён", "success")
    else:
        flash("Не удалось выбрать прогноз", "error")
    return redirect(url_for("pages.session_page", session_id=session.id))


@pages_bp.post("/sessions/import")
@login_required
def import_session_action():
    form = SessionImportForm()
    if not form.validate_on_submit():
        flash("Не удалось импортировать сессию", "error")
        return redirect(url_for("pages.dashboard"))

    raw_payload = (form.payload_json.data or "").strip()
    if not raw_payload:
        flash("Вставьте JSON экспортированной сессии", "error")
        return redirect(url_for("pages.dashboard"))

    try:
        payload = parse_json(raw_payload, field_name="session_import", default={})
        row = import_session_payload(payload)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("pages.dashboard"))

    flash(f"Сессия «{row.title}» импортирована", "success")
    return redirect(url_for("pages.session_page", session_id=row.id))


@pages_bp.get("/catalog")
@login_required
def catalog():
    rows = db.session.query(ObjectType).order_by(ObjectType.category, ObjectType.code).all()
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Справочник", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    from ..services.catalog_presenters import build_catalog_sections, glossary_groups

    return render_template(
        "core/catalog.html",
        object_types=rows,
        sections=build_catalog_sections(rows),
        glossary=glossary_groups(),
        **ctx,
    )
