from __future__ import annotations

from typing import Dict, List

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ...application.context import (
    resolve_session_analysis_context,
    update_analysis_settings_for_session,
)
from ...application.portfolio import portfolio_rows, portfolio_summary
from ...application.sessions import create_session_record, delete_session_record
from ..extensions import db
from ..forms import (
    ConfirmDeleteForm,
    ForecastSelectionForm,
    LoginForm,
    SessionForm,
    SessionImportForm,
)
from ..models import GameSession, ObjectType, Ruleset, User
from ..services.evaluation import ForecastCompatibilityError
from ..services.navigation import is_safe_internal_url, safe_next_url
from ..services.test_game_preset import (
    TEST_GAME_BUNDLED_FORECAST_NAME,
    TEST_GAME_DEFAULT_SESSION_TITLE,
    is_test_game_ruleset,
)
from ..services.lots_dashboard import (
    analytics_by_lot_for_session,
    filter_lot_rows,
    lot_rows_for_session,
    sort_lot_rows,
)
from ..services.session_io import import_session_payload
from ..services.stale import mark_stale_for_session
from ..services.strategy_catalog import strategy_list, strategy_meta
from ..services.ui_text import SESSION_TERMS, strategy_label
from .page_support import (
    forecast_compatibility_guidance,
    nav,
    parse_json,
    session_analysis_view,
    session_stale_ctx,
)
from .shared import pages_bp


def _fmt_datetime(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _missing_session_redirect():
    flash("Сессия не найдена.", "error")
    return redirect(url_for("pages.dashboard"))


def _sorted_active_rulesets() -> List[Ruleset]:
    rows = db.session.query(Ruleset).filter_by(is_active=True).all()
    return sorted(
        rows,
        key=lambda row: (
            0 if is_test_game_ruleset(row) else 1,
            str(row.name or "").lower(),
            row.id,
        ),
    )


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
    rulesets = _sorted_active_rulesets()
    default_budget = 200.0
    if rulesets:
        form.ruleset_id.choices = [
            (row.id, f"{row.name} ({row.code}:{row.version})") for row in rulesets
        ]
        if request.method == "GET":
            form.ruleset_id.data = rulesets[0].id
        cfg = dict(rulesets[0].config_json or {})
        default_budget = float(
            (cfg.get("auction", {}) or {}).get("starting_budget", 200.0) or 200.0
        )
    else:
        form.ruleset_id.choices = []
    if request.method == "GET" and not form.budget_total.data:
        form.budget_total.data = default_budget
    if request.method == "GET" and not (form.title.data or "").strip():
        form.title.data = TEST_GAME_DEFAULT_SESSION_TITLE

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
    forecast_form.selected_forecast_id.choices = [(0, TEST_GAME_BUNDLED_FORECAST_NAME)] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})") for forecast in session.forecasts
    ]
    forecast_form.selected_forecast_id.data = int(session.selected_forecast_id or 0)

    forecast_report = dict(
        (analysis_ctx["forecast_summary"] or {}).get("compatibility_report") or {}
    )
    forecast_blocked = not bool((analysis_ctx["forecast_summary"] or {}).get("is_compatible", True))
    analytics_by_lot: Dict[int, Dict[str, object]] = {}
    if not forecast_blocked:
        try:
            analytics_by_lot = analytics_by_lot_for_session(session)
        except ForecastCompatibilityError as exc:
            forecast_blocked = True
            forecast_report = dict(exc.report)
            analytics_by_lot = {}
    compatibility_guidance = forecast_compatibility_guidance(
        forecast_report,
        session=session,
    )

    portfolio = portfolio_summary(session, analytics_by_lot=analytics_by_lot)
    purchased_rows = portfolio_rows(session, analytics_by_lot=analytics_by_lot)
    rows = lot_rows_for_session(session, ranking_map=analytics_by_lot)
    sort_key = str(request.args.get("sort", "utility_desc") or "utility_desc")
    available_rows = [row for row in rows if row["status"] == "available"]
    available_rows = sort_lot_rows(filter_lot_rows(available_rows, request.args), sort_key)

    stale_ctx = session_stale_ctx(session)
    stale_warning = stale_ctx.get("stale_warning") or {}
    has_stale = bool(stale_warning.get("has_stale"))
    uses_bundled = analysis_ctx["forecast_context"]["source"] == "bundled_forecast"
    readiness_cards = [
        {
            "label": "Энергосистема",
            "value": len(session.objects),
            "hint": "объектов в модели",
            "status": "ready" if len(session.objects) > 0 else "attention",
        },
        {
            "label": "Лоты",
            "value": len(session.lots),
            "hint": "лотов в сессии",
            "status": "ready" if len(session.lots) > 0 else "attention",
        },
        {
            "label": "Прогноз",
            "value": analysis_ctx["forecast_context"]["forecast_name"],
            "hint": "встроенный базовый" if uses_bundled else "загружен пользователем",
            "status": "attention" if uses_bundled else "ready",
        },
        {
            "label": "Актуальность оценок",
            "value": "Требует внимания" if has_stale else "Готово",
            "hint": f"устаревших оценок: {int(stale_warning.get('stale_count', 0) or 0)}",
            "status": "attention" if has_stale else "ready",
        },
    ]
    kpis = {
        "lots_total": len(session.lots),
        "bought_total": int(portfolio["bought_lots_count"]),
        "portfolio_utility": float(
            sum(float(row.get("utility", 0.0) or 0.0) for row in purchased_rows)
        ),
        "portfolio_net_profit": float(portfolio["aggregate_net_profit"]),
        "risk_profile": portfolio["risk_profile_label"],
        "data_status": (
            "Готово" if len(session.lots) > 0 and len(session.objects) > 0 else "Требует внимания"
        ),
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
        kpis=kpis,
        readiness_cards=readiness_cards,
        forecast_form=forecast_form,
        forecast_card=analysis_ctx["forecast_summary"],
        portfolio=portfolio,
        available_rows=available_rows,
        purchased_rows=purchased_rows,
        sort_key=sort_key,
        filters=request.args,
        delete_url=url_for("pages.session_delete_confirm_page", session_id=session.id),
        export_json_url=url_for("api.export_session", session_id=session.id),
        export_csv_url=url_for("api.export_evaluations", session_id=session.id),
        recalculate_url=url_for("api.recalculate_session_lots", session_id=session.id),
        strategy_api_url=url_for("api.strategy_snapshot", session_id=session.id),
        forecast_blocked=forecast_blocked,
        forecast_compatibility_report=forecast_report,
        compatibility_guidance=compatibility_guidance,
        **session_analysis_view(session),
        **ctx,
        **stale_ctx,
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
    form.selected_forecast_id.choices = [(0, TEST_GAME_BUNDLED_FORECAST_NAME)] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})") for forecast in session.forecasts
    ]
    previous_forecast_id = int(session.selected_forecast_id or 0)
    if form.validate_on_submit():
        update_analysis_settings_for_session(
            session, {"selected_forecast_id": form.selected_forecast_id.data or None}
        )
        db.session.add(session)
        db.session.commit()
        if int(session.selected_forecast_id or 0) != previous_forecast_id:
            mark_stale_for_session(session.id, reason="forecast_changed")
        flash("Активный прогноз обновлён", "success")
    else:
        flash("Не удалось выбрать прогноз", "error")
    target = (request.form.get("next") or request.referrer or "").strip()
    if target and is_safe_internal_url(target):
        return redirect(target)
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
