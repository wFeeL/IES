from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..forms import (
    LoginForm,
    LotForm,
    ObjectTypeForm,
    RulesetCopyForm,
    RulesetForm,
    SessionForm,
    StartPackTemplateForm,
)
from ..models import (
    EvaluationResult,
    Forecast,
    GameSession,
    Lot,
    ObjectType,
    Ruleset,
    User,
)
from ..services.auth import role_required
from ..services.evaluation import compare_lots, recommend_best_lot, strategy_fit
from ..services.navigation import build_breadcrumbs, is_safe_internal_url, safe_back_url, safe_next_url
from ..services.network import validate_session_network
from ..services.object_type_admin import (
    create_object_type,
    deactivate_object_type,
    get_object_type_or_error,
    list_object_types,
    update_object_type,
)
from ..services.ruleset_admin import (
    activate_ruleset,
    copy_ruleset,
    create_ruleset,
    deactivate_ruleset,
    get_ruleset_or_error,
    list_rulesets,
    update_ruleset,
)
from ..services.stale import stale_summary_for_session
from ..services.start_pack import (
    create_start_pack_template,
    deactivate_start_pack_template,
    get_start_pack_template_or_error,
    list_start_pack_templates,
    update_start_pack_template,
)

pages_bp = Blueprint("pages", __name__)


def _parse_json(raw: str, *, field_name: str, default: Any) -> Any:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Некорректный JSON в поле {field_name}: {exc}") from exc


def _breadcrumbs(items: Iterable[Tuple[str, str | None, Dict[str, Any] | None]]) -> List[Dict[str, Any]]:
    return build_breadcrumbs(items)


def _session_stale_ctx(session: GameSession) -> Dict[str, Any]:
    return {"stale_warning": stale_summary_for_session(session.id)}


def _nav(
    *,
    breadcrumbs: Iterable[Tuple[str, str | None, Dict[str, Any] | None]],
    fallback_endpoint: str,
    fallback_values: Dict[str, Any] | None = None,
    cancel_url: str | None = None,
) -> Dict[str, Any]:
    return {
        "breadcrumbs": _breadcrumbs(breadcrumbs),
        "back_url": safe_back_url(
            fallback_endpoint=fallback_endpoint,
            **(fallback_values or {}),
        ),
        "cancel_url": cancel_url,
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

    return render_template("login.html", form=form, next_target=next_target)


@pages_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("pages.login"))


@pages_bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    form = SessionForm()
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
        row = GameSession(
            title=form.title.data,
            ruleset_id=form.ruleset_id.data,
            selected_strategy=form.selected_strategy.data,
            budget_total=float(form.budget_total.data or selected_budget),
            allpay_spent=0.0,
        )
        db.session.add(row)
        db.session.commit()
        return redirect(url_for("pages.session_page", session_id=row.id))

    sessions = db.session.query(GameSession).order_by(GameSession.updated_at.desc()).all()
    return render_template("dashboard.html", sessions=sessions, form=form)


@pages_bp.get("/sessions/<int:session_id>")
@login_required
def session_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template("session.html", session=session, **ctx, **_session_stale_ctx(session))


@pages_bp.get("/catalog")
@login_required
def catalog():
    rows = db.session.query(ObjectType).order_by(ObjectType.code).all()
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Справочник", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template("catalog.html", object_types=rows, **ctx)


@pages_bp.get("/system/<int:session_id>")
@login_required
def system_view(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    issues = validate_session_network(list(session.objects))
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Энергосистема", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template("system.html", session=session, issues=issues, **ctx, **_session_stale_ctx(session))


@pages_bp.get("/lots/<int:session_id>")
@login_required
def lots_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).order_by(Lot.id).all()
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template("lots.html", session=session, lots=lots, **ctx, **_session_stale_ctx(session))


@pages_bp.route("/lots/<int:session_id>/edit", methods=["GET", "POST"])
@login_required
def lots_edit(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    form = LotForm()
    form.session_id.data = session_id
    object_types = list_object_types(include_inactive=False)

    if form.validate_on_submit():
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
            items_payload = _parse_json(source, field_name="items", default=[])
            from .api import _lot_items_from_payload  # local import to avoid circular import

            _lot_items_from_payload(lot, items_payload)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            ctx = _nav(
                breadcrumbs=[
                    ("Dashboard", "pages.dashboard", None),
                    (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
                    ("Лоты", "pages.lots_page", {"session_id": session.id}),
                    ("Новый лот", None, None),
                ],
                fallback_endpoint="pages.lots_page",
                fallback_values={"session_id": session.id},
                cancel_url=url_for("pages.lots_page", session_id=session.id),
            )
            return render_template(
                "lot_edit.html",
                session=session,
                form=form,
                object_types=object_types,
                **ctx,
                **_session_stale_ctx(session),
            )

        db.session.add(lot)
        db.session.commit()
        flash("Лот сохранен", "success")
        return redirect(url_for("pages.lots_page", session_id=session_id))

    if not form.items_state_json.data:
        form.items_state_json.data = "[]"

    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            ("Новый лот", None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.lots_page", session_id=session.id),
    )
    return render_template(
        "lot_edit.html",
        session=session,
        form=form,
        object_types=object_types,
        **ctx,
        **_session_stale_ctx(session),
    )


@pages_bp.get("/forecast/<int:session_id>")
@login_required
def forecast_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    forecasts = (
        db.session.query(Forecast)
        .filter_by(session_id=session_id)
        .order_by(Forecast.id.desc())
        .all()
    )
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Прогнозы", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
        cancel_url=url_for("pages.session_page", session_id=session.id),
    )
    return render_template(
        "forecast.html",
        session=session,
        forecasts=forecasts,
        **ctx,
        **_session_stale_ctx(session),
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
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Оценки", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "evaluation.html",
        session=session,
        evaluations=evaluations,
        **ctx,
        **_session_stale_ctx(session),
    )


@pages_bp.get("/compare/<int:session_id>")
@login_required
def compare_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    ranking = compare_lots(session=session, lots=lots, mode="forecast") if lots else []
    top_pair = ranking[:2]
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Сравнение", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "compare.html",
        session=session,
        lots=lots,
        ranking=ranking,
        top_pair=top_pair,
        **ctx,
        **_session_stale_ctx(session),
    )


@pages_bp.get("/recommend/<int:session_id>")
@login_required
def recommend_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))
    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    recommendation = (
        recommend_best_lot(session=session, lots=lots, mode="forecast") if lots else None
    )
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Рекомендации", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "recommend.html",
        session=session,
        lots=lots,
        recommendation=recommendation,
        **ctx,
        **_session_stale_ctx(session),
    )


@pages_bp.get("/quick-auction/<int:session_id>")
@login_required
def quick_auction_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return redirect(url_for("pages.dashboard"))

    lots = db.session.query(Lot).filter_by(session_id=session_id).all()
    ranking = compare_lots(session=session, lots=lots, mode="forecast") if lots else []
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Quick auction", None, None),
        ],
        fallback_endpoint="pages.session_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "quick_auction.html",
        session=session,
        lots=lots,
        ranking=ranking,
        **ctx,
        **_session_stale_ctx(session),
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

    fit = strategy_fit(session=session, lot=lot, mode="forecast")
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            (f"Сессия #{session.id}", "pages.session_page", {"session_id": session.id}),
            ("Лоты", "pages.lots_page", {"session_id": session.id}),
            (f"Strategy fit / Lot {lot.id}", None, None),
        ],
        fallback_endpoint="pages.lots_page",
        fallback_values={"session_id": session.id},
    )
    return render_template(
        "strategy_fit.html",
        session=session,
        lot=lot,
        fit=fit,
        **ctx,
        **_session_stale_ctx(session),
    )


@pages_bp.get("/settings/model")
@login_required
@role_required("admin")
def settings_model_page():
    rulesets = list_rulesets()
    templates = list_start_pack_templates(include_inactive=True)
    copy_form = RulesetCopyForm()
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Настройки модели", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template(
        "settings_model.html",
        rulesets=rulesets,
        templates=templates,
        copy_form=copy_form,
        **ctx,
    )


@pages_bp.route("/settings/model/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_ruleset_new_page():
    form = RulesetForm()
    templates = list_start_pack_templates(include_inactive=False)
    form.active_start_pack_template_id.choices = [(0, "-- Без шаблона --")] + [
        (row.id, f"{row.name} ({row.code})") for row in templates
    ]

    if request.method == "GET":
        form.config_json.data = "{}"
        form.model_settings_json.data = "{}"

    if form.validate_on_submit():
        try:
            payload = {
                "code": form.code.data,
                "version": form.version.data,
                "name": form.name.data,
                "config_json": _parse_json(form.config_json.data or "{}", field_name="config", default={}),
                "model_settings": _parse_json(
                    form.model_settings_json.data or "{}",
                    field_name="model_settings",
                    default={},
                ),
                "active_start_pack_template_id": form.active_start_pack_template_id.data or None,
                "is_active": bool(form.is_active.data),
                "is_builtin": bool(form.is_builtin.data),
            }
            create_ruleset(payload)
            flash("Ruleset создан", "success")
            return redirect(url_for("pages.settings_model_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Настройки модели", "pages.settings_model_page", None),
            ("Новый ruleset", None, None),
        ],
        fallback_endpoint="pages.settings_model_page",
        cancel_url=url_for("pages.settings_model_page"),
    )
    return render_template("settings_ruleset_edit.html", form=form, mode="new", **ctx)


@pages_bp.route("/settings/model/<int:ruleset_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_ruleset_edit_page(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    form = RulesetForm()
    templates = list_start_pack_templates(include_inactive=True)
    form.active_start_pack_template_id.choices = [(0, "-- Без шаблона --")] + [
        (it.id, f"{it.name} ({it.code})") for it in templates
    ]

    if request.method == "GET":
        form.code.data = row.code
        form.version.data = row.version
        form.name.data = row.name
        form.config_json.data = json.dumps(row.config_json or {}, ensure_ascii=False, indent=2)
        form.model_settings_json.data = json.dumps(
            row.model_settings_json or {},
            ensure_ascii=False,
            indent=2,
        )
        form.active_start_pack_template_id.data = row.active_start_pack_template_id or 0
        form.is_active.data = bool(row.is_active)
        form.is_builtin.data = bool(row.is_builtin)

    if form.validate_on_submit():
        try:
            update_ruleset(
                row,
                {
                    "name": form.name.data,
                    "config_json": _parse_json(
                        form.config_json.data or "{}",
                        field_name="config",
                        default={},
                    ),
                    "model_settings": _parse_json(
                        form.model_settings_json.data or "{}",
                        field_name="model_settings",
                        default={},
                    ),
                    "active_start_pack_template_id": form.active_start_pack_template_id.data or None,
                    "is_active": bool(form.is_active.data),
                },
            )
            flash("Ruleset обновлен", "success")
            return redirect(url_for("pages.settings_model_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Настройки модели", "pages.settings_model_page", None),
            (f"Ruleset {row.code}:{row.version}", None, None),
        ],
        fallback_endpoint="pages.settings_model_page",
        cancel_url=url_for("pages.settings_model_page"),
    )
    return render_template("settings_ruleset_edit.html", form=form, mode="edit", ruleset=row, **ctx)


@pages_bp.post("/settings/model/<int:ruleset_id>/copy")
@login_required
@role_required("admin")
def settings_ruleset_copy_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    form = RulesetCopyForm()
    if form.validate_on_submit():
        try:
            copy_ruleset(
                row,
                name=form.name.data,
                code=form.code.data,
            )
            flash("Ruleset скопирован", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Некорректные данные формы копирования", "error")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.post("/settings/model/<int:ruleset_id>/activate")
@login_required
@role_required("admin")
def settings_ruleset_activate_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    activate_ruleset(row)
    flash("Ruleset активирован", "success")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.post("/settings/model/<int:ruleset_id>/deactivate")
@login_required
@role_required("admin")
def settings_ruleset_deactivate_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    try:
        deactivate_ruleset(row)
        flash("Ruleset деактивирован", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.get("/settings/start-packs")
@login_required
@role_required("admin")
def settings_start_packs_page():
    templates = list_start_pack_templates(include_inactive=True)
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Шаблоны стартового пакета", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template("settings_start_packs.html", templates=templates, **ctx)


@pages_bp.route("/settings/start-packs/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_start_pack_new_page():
    form = StartPackTemplateForm()
    if request.method == "GET":
        form.items_state_json.data = "[]"
        form.items_json.data = "[]"

    if form.validate_on_submit():
        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = _parse_json(source, field_name="start_pack.items", default=[])
            create_start_pack_template(
                code=form.code.data,
                name=form.name.data,
                description=form.description.data or "",
                is_active=bool(form.is_active.data),
                is_builtin=bool(form.is_builtin.data),
                items_payload=items_payload,
            )
            flash("Шаблон стартового пакета создан", "success")
            return redirect(url_for("pages.settings_start_packs_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    object_types = list_object_types(include_inactive=False)
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Шаблоны стартового пакета", "pages.settings_start_packs_page", None),
            ("Новый шаблон", None, None),
        ],
        fallback_endpoint="pages.settings_start_packs_page",
        cancel_url=url_for("pages.settings_start_packs_page"),
    )
    return render_template(
        "settings_start_pack_edit.html",
        form=form,
        mode="new",
        object_types=object_types,
        **ctx,
    )


@pages_bp.route("/settings/start-packs/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_start_pack_edit_page(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    form = StartPackTemplateForm()

    if request.method == "GET":
        form.code.data = row.code
        form.name.data = row.name
        form.description.data = row.description
        form.is_active.data = bool(row.is_active)
        form.is_builtin.data = bool(row.is_builtin)
        items_payload = [item.to_dict() for item in row.items]
        for idx, item in enumerate(items_payload, start=1):
            item["row_key"] = str(item["id"])
            item["parent_key"] = str(item["parent_item_id"]) if item.get("parent_item_id") else None
            item["sort_order"] = item.get("sort_order", idx * 10)
        form.items_state_json.data = json.dumps(items_payload, ensure_ascii=False)
        form.items_json.data = json.dumps(items_payload, ensure_ascii=False, indent=2)

    if form.validate_on_submit():
        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = _parse_json(source, field_name="start_pack.items", default=[])
            update_start_pack_template(
                row=row,
                payload={
                    "code": form.code.data,
                    "name": form.name.data,
                    "description": form.description.data or "",
                    "is_active": bool(form.is_active.data),
                    "is_builtin": bool(form.is_builtin.data),
                    "items": items_payload,
                },
            )
            flash("Шаблон стартового пакета обновлен", "success")
            return redirect(url_for("pages.settings_start_packs_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    object_types = list_object_types(include_inactive=True)
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Шаблоны стартового пакета", "pages.settings_start_packs_page", None),
            (row.name, None, None),
        ],
        fallback_endpoint="pages.settings_start_packs_page",
        cancel_url=url_for("pages.settings_start_packs_page"),
    )
    return render_template(
        "settings_start_pack_edit.html",
        form=form,
        mode="edit",
        object_types=object_types,
        template=row,
        **ctx,
    )


@pages_bp.post("/settings/start-packs/<int:template_id>/deactivate")
@login_required
@role_required("admin")
def settings_start_pack_deactivate_action(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    deactivate_start_pack_template(row)
    flash("Шаблон стартового пакета деактивирован", "success")
    return redirect(url_for("pages.settings_start_packs_page"))


@pages_bp.get("/settings/object-types")
@login_required
@role_required("admin")
def settings_object_types_page():
    rows = list_object_types(include_inactive=True)
    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Типы объектов", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template("settings_object_types.html", object_types=rows, **ctx)


@pages_bp.route("/settings/object-types/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_object_type_new_page():
    form = ObjectTypeForm()
    if request.method == "GET":
        form.default_parameters_json.data = "{}"
        form.editable_fields_json.data = "[]"
        form.rules_json.data = "{}"

    if form.validate_on_submit():
        try:
            create_object_type(
                {
                    "code": form.code.data,
                    "name": form.name.data,
                    "category": form.category.data,
                    "subtype": form.subtype.data,
                    "description": form.description.data,
                    "default_parameters": _parse_json(
                        form.default_parameters_json.data or "{}",
                        field_name="default_parameters",
                        default={},
                    ),
                    "editable_fields": _parse_json(
                        form.editable_fields_json.data or "[]",
                        field_name="editable_fields",
                        default=[],
                    ),
                    "rules": _parse_json(
                        form.rules_json.data or "{}",
                        field_name="rules",
                        default={},
                    ),
                    "is_active": bool(form.is_active.data),
                }
            )
            flash("Тип объекта создан", "success")
            return redirect(url_for("pages.settings_object_types_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Типы объектов", "pages.settings_object_types_page", None),
            ("Новый тип", None, None),
        ],
        fallback_endpoint="pages.settings_object_types_page",
        cancel_url=url_for("pages.settings_object_types_page"),
    )
    return render_template("settings_object_type_edit.html", form=form, mode="new", **ctx)


@pages_bp.route("/settings/object-types/<int:object_type_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_object_type_edit_page(object_type_id: int):
    row = get_object_type_or_error(object_type_id)
    form = ObjectTypeForm()

    if request.method == "GET":
        form.code.data = row.code
        form.name.data = row.name
        form.category.data = row.category
        form.subtype.data = row.subtype
        form.description.data = row.description
        form.default_parameters_json.data = json.dumps(
            row.default_parameters_json or {},
            ensure_ascii=False,
            indent=2,
        )
        form.editable_fields_json.data = json.dumps(
            row.editable_fields_json or [],
            ensure_ascii=False,
            indent=2,
        )
        form.rules_json.data = json.dumps(row.rules_json or {}, ensure_ascii=False, indent=2)
        form.is_active.data = bool(row.is_active)

    if form.validate_on_submit():
        try:
            update_object_type(
                row,
                {
                    "name": form.name.data,
                    "category": form.category.data,
                    "subtype": form.subtype.data,
                    "description": form.description.data,
                    "default_parameters": _parse_json(
                        form.default_parameters_json.data or "{}",
                        field_name="default_parameters",
                        default={},
                    ),
                    "editable_fields": _parse_json(
                        form.editable_fields_json.data or "[]",
                        field_name="editable_fields",
                        default=[],
                    ),
                    "rules": _parse_json(
                        form.rules_json.data or "{}",
                        field_name="rules",
                        default={},
                    ),
                    "is_active": bool(form.is_active.data),
                },
            )
            flash("Тип объекта обновлен", "success")
            return redirect(url_for("pages.settings_object_types_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = _nav(
        breadcrumbs=[
            ("Dashboard", "pages.dashboard", None),
            ("Типы объектов", "pages.settings_object_types_page", None),
            (row.code, None, None),
        ],
        fallback_endpoint="pages.settings_object_types_page",
        cancel_url=url_for("pages.settings_object_types_page"),
    )
    return render_template(
        "settings_object_type_edit.html",
        form=form,
        mode="edit",
        object_type=row,
        **ctx,
    )


@pages_bp.post("/settings/object-types/<int:object_type_id>/deactivate")
@login_required
@role_required("admin")
def settings_object_type_deactivate_action(object_type_id: int):
    row = get_object_type_or_error(object_type_id)
    deactivate_object_type(row)
    flash("Тип объекта деактивирован", "success")
    return redirect(url_for("pages.settings_object_types_page"))
