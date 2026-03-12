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
    update_session_object,
)
from ...application.portfolio import (
    buy_lot,
    portfolio_summary,
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
from ..services.forecast_service import summarize_forecast
from ..services.lots_dashboard import (
    analytics_by_lot_for_session,
    filter_lot_rows,
    lot_rows_for_session,
    lot_summary,
    sort_lot_rows,
)
from ..services.network import validate_session_network
from ..services.object_instance_editor import parameter_rows, parameters_from_form
from ..services.stale import mark_stale_for_session
from .api_support import lot_items_from_payload
from .page_support import nav, parse_json, session_analysis_view, session_stale_ctx
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
    ]
    if current_type_id is not None and current_type_id not in {row_id for row_id, _ in choices}:
        row = db.session.get(ObjectType, int(current_type_id))
        if row is not None:
            choices.append((row.id, f"{row.name} ({row.code})"))
    return choices


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
    name = forecast.get("name") or "Встроенный базовый прогноз"
    tick_from = forecast.get("tick_from") or "—"
    tick_to = forecast.get("tick_to") or "—"
    return f"Анализ выполнен по прогнозу: {name}, такты {tick_from}–{tick_to}."


def _render_object_editor(*, session: GameSession, form: ObjectInstanceForm, obj: ObjectInstance | None):
    object_type = _object_type_by_form_value(form.object_type_id.data)
    current_parameters = obj.current_parameters_json if obj is not None else {}
    submitted_values = dict(request.form) if request.method == "POST" else None
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
        parameter_rows=parameter_rows(
            object_type,
            current_parameters=current_parameters,
            submitted_values=submitted_values,
        ),
        editor_mode="edit" if obj is not None else "create",
        show_analysis_context=False,
        **session_analysis_view(session),
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
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/system/<int:session_id>")
@login_required
def system_view(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()
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
        object_rows=session.objects,
        show_analysis_context=False,
        **session_analysis_view(session),
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
        form.object_type_id.data = requested_type_id if requested_type_id in available_type_ids else form.object_type_id.choices[0][0]
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
        form.object_type_id.data = requested_type_id if requested_type_id in available_type_ids else obj.object_type_id
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
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/lots/<int:session_id>")
@login_required
def lots_page(session_id: int):
    session = db.session.get(GameSession, session_id)
    if session is None:
        return _missing_session_redirect()

    ranking_map = analytics_by_lot_for_session(session)
    rows = lot_rows_for_session(session, ranking_map=ranking_map)
    rows = filter_lot_rows(rows, request.args)
    sort_key = str(request.args.get("sort", "utility_desc") or "utility_desc")
    rows = sort_lot_rows(rows, sort_key)
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
        show_analysis_context=False,
        **session_analysis_view(session),
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

    evaluation = evaluate_session_lot(session=session, lot=lot, persist=False)
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
        forecast_line=_forecast_line(session),
        show_analysis_context=False,
        **session_analysis_view(session),
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
        **session_analysis_view(session),
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

    evaluation = evaluate_session_lot(session=session, lot=lot, persist=False)
    portfolio = portfolio_summary(session)
    form = LotPurchaseForm()
    if request.method == "GET":
        form.purchase_price.data = float(lot.current_bid or 0.0)
    if form.validate_on_submit():
        try:
            summary = buy_lot(session, lot, float(form.purchase_price.data or 0.0))
            db.session.commit()
            flash(f"Лот «{lot.name}» куплен по цене {summary['purchase_price']:.1f}", "success")
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
        portfolio=portfolio,
        show_analysis_context=False,
        forecast_line=_forecast_line(session),
        **session_analysis_view(session),
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
            flash(f"Покупка лота «{lot.name}» отменена, бюджет восстановлен до {summary['remaining_budget']:.1f}", "success")
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
    object_types = list_object_types_for_admin(include_inactive=False)

    if form.validate_on_submit():
        lot = Lot(
            session_id=session_id,
            name=form.name.data,
            scope=form.scope.data,
            status="available",
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
            lot_items_from_payload(lot, items_payload)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _render_lot_editor(session=session, form=form, object_types=object_types, lot=None)

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
            default_items = [{"object_type_id": int(object_types[0].id), "quantity": 1, "overrides": {}}]
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
    object_types = list_object_types_for_admin(include_inactive=False)

    if request.method == "GET":
        form.name.data = lot.name
        form.scope.data = lot.scope
        form.base_bid.data = lot.base_bid
        form.current_bid.data = lot.current_bid
        form.available_round.data = lot.available_round
        form.note.data = lot.note
        items_payload = _lot_items_payload(lot)
        form.items_state_json.data = json.dumps(items_payload, ensure_ascii=False)
        form.items_json.data = json.dumps(items_payload, ensure_ascii=False, indent=2)

    if form.validate_on_submit():
        lot.name = form.name.data
        lot.scope = form.scope.data
        lot.base_bid = float(form.base_bid.data or 0.0)
        lot.current_bid = float(form.current_bid.data or 0.0)
        lot.available_round = int(form.available_round.data or 1)
        lot.note = form.note.data or ""

        source = (form.items_state_json.data or "").strip() or (form.items_json.data or "").strip()
        try:
            items_payload = parse_json(source, field_name="items", default=[])
            lot_items_from_payload(lot, items_payload)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _render_lot_editor(session=session, form=form, object_types=object_types, lot=lot)

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
    forecasts = db.session.query(Forecast).filter_by(session_id=session_id).order_by(Forecast.id.desc()).all()
    analysis_ctx = session_analysis_view(session)
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
    return render_template(
        "analysis/forecast.html",
        session=session,
        forecasts=forecasts,
        active_forecast_id=int(session.selected_forecast_id or 0),
        active_forecast_summary=analysis_ctx["forecast_summary"],
        forecast_summaries={forecast.id: summarize_forecast(forecast) for forecast in forecasts},
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

    ranking = rank_session_lots(
        session=session,
        lots=[lot for lot in session.lots if lot.status == "available"],
        persist=False,
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
        lots=[lot for lot in session.lots if lot.status == "available"],
        ranking=ranking,
        forecast_line=_forecast_line(session),
        buy_form=LotPurchaseForm(),
        show_analysis_context=False,
        **session_analysis_view(session),
        **ctx,
        **session_stale_ctx(session),
    )


@pages_bp.get("/strategy-fit/<int:lot_id>")
@login_required
def strategy_fit_page(lot_id: int):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        return _missing_lot_redirect(lot_id=lot_id)
    flash("Страница «Strategy fit» выведена из основного сценария. Открыта карточка лота.", "warning")
    return redirect(url_for("pages.lot_detail_page", lot_id=lot.id))
