from __future__ import annotations

import json

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..forms import ObjectTypeForm, RulesetCopyForm, RulesetForm, StartPackTemplateForm
from ..services.auth import role_required
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
from ..services.start_pack import (
    create_start_pack_template,
    deactivate_start_pack_template,
    get_start_pack_template_or_error,
    list_start_pack_templates,
    update_start_pack_template,
)
from .page_support import admin_links, nav, parse_json
from .shared import pages_bp


@pages_bp.get("/settings/model")
@pages_bp.get("/admin/rulesets")
@login_required
@role_required("admin")
def settings_model_page():
    rulesets = list_rulesets()
    templates = list_start_pack_templates(include_inactive=True)
    copy_form = RulesetCopyForm()
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Наборы правил", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template(
        "admin/settings_model.html",
        rulesets=rulesets,
        templates=templates,
        copy_form=copy_form,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/model/new", methods=["GET", "POST"])
@pages_bp.route("/admin/rulesets/new", methods=["GET", "POST"])
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
                "config_json": parse_json(form.config_json.data or "{}", field_name="config", default={}),
                "model_settings": parse_json(
                    form.model_settings_json.data or "{}",
                    field_name="model_settings",
                    default={},
                ),
                "active_start_pack_template_id": form.active_start_pack_template_id.data or None,
                "is_active": bool(form.is_active.data),
                "is_builtin": bool(form.is_builtin.data),
            }
            create_ruleset(payload)
            flash("Набор правил создан", "success")
            return redirect(url_for("pages.settings_model_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Новый набор правил", None, None),
        ],
        fallback_endpoint="pages.settings_model_page",
        cancel_url=url_for("pages.settings_model_page"),
    )
    return render_template(
        "admin/settings_ruleset_edit.html",
        form=form,
        mode="new",
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/model/<int:ruleset_id>/edit", methods=["GET", "POST"])
@pages_bp.route("/admin/rulesets/<int:ruleset_id>/edit", methods=["GET", "POST"])
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
                    "config_json": parse_json(form.config_json.data or "{}", field_name="config", default={}),
                    "model_settings": parse_json(
                        form.model_settings_json.data or "{}",
                        field_name="model_settings",
                        default={},
                    ),
                    "active_start_pack_template_id": form.active_start_pack_template_id.data or None,
                    "is_active": bool(form.is_active.data),
                },
            )
            flash("Набор правил обновлен", "success")
            return redirect(url_for("pages.settings_model_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", "pages.settings_model_page", None),
            (f"{row.code}:{row.version}", None, None),
        ],
        fallback_endpoint="pages.settings_model_page",
        cancel_url=url_for("pages.settings_model_page"),
    )
    return render_template(
        "admin/settings_ruleset_edit.html",
        form=form,
        mode="edit",
        ruleset=row,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.post("/settings/model/<int:ruleset_id>/copy")
@pages_bp.post("/admin/rulesets/<int:ruleset_id>/copy")
@login_required
@role_required("admin")
def settings_ruleset_copy_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    form = RulesetCopyForm()
    if form.validate_on_submit():
        try:
            copy_ruleset(row, name=form.name.data, code=form.code.data)
            flash("Набор правил скопирован", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    else:
        flash("Некорректные данные формы копирования", "error")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.post("/settings/model/<int:ruleset_id>/activate")
@pages_bp.post("/admin/rulesets/<int:ruleset_id>/activate")
@login_required
@role_required("admin")
def settings_ruleset_activate_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    activate_ruleset(row)
    flash("Набор правил активирован", "success")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.post("/settings/model/<int:ruleset_id>/deactivate")
@pages_bp.post("/admin/rulesets/<int:ruleset_id>/deactivate")
@login_required
@role_required("admin")
def settings_ruleset_deactivate_action(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    try:
        deactivate_ruleset(row)
        flash("Набор правил деактивирован", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.get("/settings/start-packs")
@pages_bp.get("/admin/start-packs")
@login_required
@role_required("admin")
def settings_start_packs_page():
    templates = list_start_pack_templates(include_inactive=True)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Стартовые пакеты", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template(
        "admin/settings_start_packs.html",
        templates=templates,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/start-packs/new", methods=["GET", "POST"])
@pages_bp.route("/admin/start-packs/new", methods=["GET", "POST"])
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
            items_payload = parse_json(source, field_name="start_pack.items", default=[])
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
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Новый шаблон", None, None),
        ],
        fallback_endpoint="pages.settings_start_packs_page",
        cancel_url=url_for("pages.settings_start_packs_page"),
    )
    return render_template(
        "admin/settings_start_pack_edit.html",
        form=form,
        mode="new",
        object_types=object_types,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/start-packs/<int:template_id>/edit", methods=["GET", "POST"])
@pages_bp.route("/admin/start-packs/<int:template_id>/edit", methods=["GET", "POST"])
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
            items_payload = parse_json(source, field_name="start_pack.items", default=[])
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
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            (row.name, None, None),
        ],
        fallback_endpoint="pages.settings_start_packs_page",
        cancel_url=url_for("pages.settings_start_packs_page"),
    )
    return render_template(
        "admin/settings_start_pack_edit.html",
        form=form,
        mode="edit",
        object_types=object_types,
        template=row,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.post("/settings/start-packs/<int:template_id>/deactivate")
@pages_bp.post("/admin/start-packs/<int:template_id>/deactivate")
@login_required
@role_required("admin")
def settings_start_pack_deactivate_action(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    deactivate_start_pack_template(row)
    flash("Шаблон стартового пакета деактивирован", "success")
    return redirect(url_for("pages.settings_start_packs_page"))


@pages_bp.get("/settings/object-types")
@pages_bp.get("/admin/object-types")
@login_required
@role_required("admin")
def settings_object_types_page():
    rows = list_object_types(include_inactive=True)
    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Типы объектов", None, None),
        ],
        fallback_endpoint="pages.dashboard",
    )
    return render_template(
        "admin/settings_object_types.html",
        object_types=rows,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/object-types/new", methods=["GET", "POST"])
@pages_bp.route("/admin/object-types/new", methods=["GET", "POST"])
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
                    "default_parameters": parse_json(
                        form.default_parameters_json.data or "{}",
                        field_name="default_parameters",
                        default={},
                    ),
                    "editable_fields": parse_json(
                        form.editable_fields_json.data or "[]",
                        field_name="editable_fields",
                        default=[],
                    ),
                    "rules": parse_json(
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

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            ("Новый тип", None, None),
        ],
        fallback_endpoint="pages.settings_object_types_page",
        cancel_url=url_for("pages.settings_object_types_page"),
    )
    return render_template(
        "admin/settings_object_type_edit.html",
        form=form,
        mode="new",
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/object-types/<int:object_type_id>/edit", methods=["GET", "POST"])
@pages_bp.route("/admin/object-types/<int:object_type_id>/edit", methods=["GET", "POST"])
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
                    "default_parameters": parse_json(
                        form.default_parameters_json.data or "{}",
                        field_name="default_parameters",
                        default={},
                    ),
                    "editable_fields": parse_json(
                        form.editable_fields_json.data or "[]",
                        field_name="editable_fields",
                        default=[],
                    ),
                    "rules": parse_json(
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

    ctx = nav(
        breadcrumb_items=[
            ("Сессии", "pages.dashboard", None),
            ("Администрирование", None, None),
            (row.code, None, None),
        ],
        fallback_endpoint="pages.settings_object_types_page",
        cancel_url=url_for("pages.settings_object_types_page"),
    )
    return render_template(
        "admin/settings_object_type_edit.html",
        form=form,
        mode="edit",
        object_type=row,
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.post("/settings/object-types/<int:object_type_id>/deactivate")
@pages_bp.post("/admin/object-types/<int:object_type_id>/deactivate")
@login_required
@role_required("admin")
def settings_object_type_deactivate_action(object_type_id: int):
    row = get_object_type_or_error(object_type_id)
    deactivate_object_type(row)
    flash("Тип объекта деактивирован", "success")
    return redirect(url_for("pages.settings_object_types_page"))
