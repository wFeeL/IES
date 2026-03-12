from __future__ import annotations

from typing import Dict, List

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ...application.context import (
    default_corridor_settings_for_ruleset,
    session_analysis_settings_for_session,
    update_analysis_settings_for_session,
)
from ...application.sessions import create_session_record, delete_session_record
from ..extensions import db
from ..forms import (
    AnalysisModeForm,
    ConfirmDeleteForm,
    CorridorSettingsForm,
    ForecastSelectionForm,
    LoginForm,
    SessionForm,
    SessionImportForm,
)
from ..models import GameSession, ObjectType, Ruleset, User
from ..services.navigation import is_safe_internal_url, safe_next_url
from ..services.session_io import import_session_payload
from ..services.strategy_catalog import strategy_list, strategy_meta
from ..services.ui_text import SESSION_TERMS, analysis_mode_label, strategy_label
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
        out.append(
            {
                "id": session.id,
                "title": session.title,
                "strategy": session.selected_strategy,
                "strategy_label": strategy_label(session.selected_strategy),
                "budget_total": float(session.budget_total or 0.0),
                "lots_count": len(session.lots),
                "objects_count": len(session.objects),
                "has_forecast": bool(session.selected_forecast_id or session.forecasts),
                "analysis_mode": session.analysis_mode,
                "analysis_mode_label": analysis_mode_label(session.analysis_mode),
                "updated_at_label": _fmt_datetime(session.updated_at),
                "url": url_for("pages.session_page", session_id=session.id),
                "delete_url": url_for("pages.session_delete_confirm_page", session_id=session.id),
            }
        )
    return out


def _workbench_forms(session: GameSession) -> Dict[str, object]:
    settings = session_analysis_settings_for_session(session)

    mode_form = AnalysisModeForm()
    mode_form.analysis_mode.data = settings["analysis_mode"]

    corridor_form = CorridorSettingsForm()
    corridor_form.consumer_load_pct.data = settings["corridor_settings"]["consumer_load_pct"]
    corridor_form.producer_generation_pct.data = settings["corridor_settings"][
        "producer_generation_pct"
    ]
    corridor_form.solar_output_pct.data = settings["corridor_settings"]["solar_output_pct"]
    corridor_form.wind_output_pct.data = settings["corridor_settings"]["wind_output_pct"]

    forecast_form = ForecastSelectionForm()
    forecast_form.selected_forecast_id.choices = [(0, "Не выбран")] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})")
        for forecast in session.forecasts
    ]
    forecast_form.selected_forecast_id.data = int(settings["selected_forecast_id"] or 0)

    return {
        "mode_form": mode_form,
        "corridor_form": corridor_form,
        "forecast_form": forecast_form,
    }


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
        auction_cfg = dict(cfg.get("auction", {}) or {})
        default_budget = float(auction_cfg.get("starting_budget", 200.0) or 200.0)
    else:
        form.ruleset_id.choices = []
    if request.method == "GET" and not form.budget_total.data:
        form.budget_total.data = default_budget

    if form.validate_on_submit():
        selected_ruleset = db.session.get(Ruleset, int(form.ruleset_id.data))
        selected_cfg = dict((selected_ruleset.config_json or {}) if selected_ruleset else {})
        selected_auction = dict(selected_cfg.get("auction", {}) or {})
        selected_budget = float(selected_auction.get("starting_budget", 200.0) or 200.0)
        row = create_session_record(
            {
                "title": form.title.data,
                "ruleset_id": form.ruleset_id.data,
                "selected_strategy": form.selected_strategy.data,
                "analysis_mode": "no_forecast",
                "corridor_settings": default_corridor_settings_for_ruleset(selected_cfg),
                "budget_total": float(form.budget_total.data or selected_budget),
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
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            (f"Сессия #{session.id}", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    readiness = {
        "objects_count": len(session.objects),
        "lots_count": len(session.lots),
        "forecasts_count": len(session.forecasts),
        "selected_forecast_name": session.selected_forecast.name
        if session.selected_forecast is not None
        else None,
    }
    quick_links = [
        {"label": "Лоты", "url": url_for("pages.lots_page", session_id=session.id)},
        {
            "label": "История оценок",
            "url": url_for("pages.evaluation_page", session_id=session.id),
        },
        {
            "label": "Быстрый аукцион",
            "url": url_for("pages.quick_auction_page", session_id=session.id),
        },
        {"label": "Сравнение", "url": url_for("pages.compare_page", session_id=session.id)},
        {
            "label": "Рекомендации",
            "url": url_for("pages.recommend_page", session_id=session.id),
        },
    ]
    return render_template(
        "core/workbench.html",
        session=session,
        readiness=readiness,
        quick_links=quick_links,
        delete_url=url_for("pages.session_delete_confirm_page", session_id=session.id),
        export_json_url=url_for("api.export_session", session_id=session.id),
        export_csv_url=url_for("api.export_evaluations", session_id=session.id),
        **_workbench_forms(session),
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


@pages_bp.post("/sessions/<int:session_id>/analysis-mode")
@login_required
def session_analysis_mode_action(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    form = AnalysisModeForm()
    if form.validate_on_submit():
        update_analysis_settings_for_session(session, {"analysis_mode": form.analysis_mode.data})
        db.session.add(session)
        db.session.commit()
        flash("Режим анализа обновлен", "success")
    else:
        flash("Не удалось обновить режим анализа", "error")
    return redirect(url_for("pages.session_page", session_id=session.id))


@pages_bp.post("/sessions/<int:session_id>/corridor")
@login_required
def session_corridor_action(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    form = CorridorSettingsForm()
    if form.validate_on_submit():
        update_analysis_settings_for_session(
            session,
            {
                "corridor_settings": {
                    "consumer_load_pct": form.consumer_load_pct.data,
                    "producer_generation_pct": form.producer_generation_pct.data,
                    "solar_output_pct": form.solar_output_pct.data,
                    "wind_output_pct": form.wind_output_pct.data,
                }
            },
        )
        db.session.add(session)
        db.session.commit()
        flash("Коридор неопределенности сохранен", "success")
    else:
        flash("Не удалось сохранить коридор", "error")
    return redirect(url_for("pages.session_page", session_id=session.id))


@pages_bp.post("/sessions/<int:session_id>/forecast-selection")
@login_required
def session_forecast_selection_action(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
    form = ForecastSelectionForm()
    form.selected_forecast_id.choices = [(0, "Не выбран")] + [
        (forecast.id, f"{forecast.name} ({forecast.source_file})")
        for forecast in session.forecasts
    ]
    if form.validate_on_submit():
        update_analysis_settings_for_session(
            session,
            {"selected_forecast_id": form.selected_forecast_id.data or None},
        )
        db.session.add(session)
        db.session.commit()
        flash("Активный прогноз обновлен", "success")
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
