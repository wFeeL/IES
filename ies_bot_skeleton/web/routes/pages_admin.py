from __future__ import annotations

import json

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from ...application.admin import (
    activate_ruleset_for_admin,
    build_default_ruleset_payload,
    copy_ruleset_for_admin,
    create_object_type_for_admin,
    create_ruleset_for_admin,
    create_start_pack_template_for_admin,
    deactivate_object_type_for_admin,
    deactivate_ruleset_for_admin,
    deactivate_start_pack_template_for_admin,
    get_object_type_for_admin,
    get_ruleset_for_admin,
    get_start_pack_template_for_admin,
    list_object_types_for_admin,
    list_rulesets_for_admin,
    list_start_pack_templates_for_admin,
    update_object_type_for_admin,
    update_ruleset_for_admin,
    update_start_pack_template_for_admin,
)
from ..forms import ObjectTypeForm, RulesetCopyForm, RulesetForm, StartPackTemplateForm
from ..services.admin_schemas import (
    OBJECT_TYPE_SUBTYPE_CHOICES,
    object_type_field_value,
    object_type_payload_from_request,
    object_type_rule_definitions,
    object_type_section_definitions,
    ruleset_form_values,
    ruleset_payload_from_request,
    ruleset_sections,
    strategy_profile_matrix,
)
from ..services.auth import role_required
from .page_support import admin_links, nav, parse_json
from .shared import pages_bp


def _admin_missing_redirect(message: str, endpoint: str):
    flash(message, "error")
    return redirect(url_for(endpoint))


def _prepare_object_type_form(form: ObjectTypeForm, row=None) -> None:
    if request.method == "GET":
        form.category.data = (row.category if row is not None else form.category.data) or "consumer"
        form.subtype.data = (row.subtype if row is not None else form.subtype.data) or ""
    current_subtype = (form.subtype.data or (row.subtype if row is not None else "") or "").strip()
    choices = list(OBJECT_TYPE_SUBTYPE_CHOICES)
    if current_subtype and current_subtype not in {value for value, _ in choices}:
        choices.append((current_subtype, current_subtype))
    form.subtype.choices = choices


def _object_type_editor_state(form: ObjectTypeForm, row=None):
    category = (form.category.data or (row.category if row is not None else "consumer") or "consumer").strip()
    subtype = (form.subtype.data or (row.subtype if row is not None else "") or "").strip()
    defaults = dict(row.default_parameters_json or {}) if row is not None else {}
    rules = dict(row.rules_json or {}) if row is not None else {}
    editable_fields = set(row.editable_fields_json or []) if row is not None else set()

    sections = []
    for section in object_type_section_definitions():
        section_visible = category in section["categories"] and (
            not section.get("subtypes") or subtype in section["subtypes"]
        )
        field_rows = []
        for field_key, field_type, label in section["fields"]:
            if request.method == "POST":
                if field_type == "boolean":
                    value = request.form.get(field_key) in {"1", "true", "on", "yes"}
                else:
                    value = request.form.get(field_key, "")
                editable_checked = request.form.get(f"editable_{field_key}") in {
                    "1",
                    "true",
                    "on",
                    "yes",
                }
            else:
                value = object_type_field_value(defaults, rules, field_key, field_type)
                editable_checked = field_key in editable_fields
            field_rows.append(
                {
                    "key": field_key,
                    "type": field_type,
                    "label": label,
                    "value": value,
                    "editable_checked": editable_checked,
                }
            )
        sections.append(
            {
                "key": section["key"],
                "title": section["title"],
                "visible": section_visible,
                "categories": list(section["categories"]),
                "subtypes": list(section.get("subtypes", [])),
                "fields": field_rows,
            }
        )

    rule_rows = []
    for field_key, label in object_type_rule_definitions():
        if request.method == "POST":
            checked = request.form.get(field_key) in {"1", "true", "on", "yes"}
        else:
            checked = bool(rules.get(field_key))
        rule_rows.append({"key": field_key, "label": label, "checked": checked})

    return {
        "category": category,
        "subtype": subtype,
        "object_type_sections": sections,
        "rule_rows": rule_rows,
    }


def _ruleset_values_for_page(base_config, base_model_settings):
    values = ruleset_form_values(base_config, base_model_settings)
    for section in ruleset_sections():
        for field_key, _, _ in section["fields"]:
            if field_key in request.form:
                values[field_key] = request.form.get(field_key)
    for row in strategy_profile_matrix():
        for weight in row["weights"]:
            field_key = f"profile__{row['code']}__{weight['key']}"
            if field_key in request.form:
                values[field_key] = request.form.get(field_key)
    return values


@pages_bp.get("/settings/model")
@pages_bp.get("/admin/rulesets")
@login_required
@role_required("admin")
def settings_model_page():
    rulesets = list_rulesets_for_admin()
    templates = list_start_pack_templates_for_admin(include_inactive=True)
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
    templates = list_start_pack_templates_for_admin(include_inactive=False)
    form.active_start_pack_template_id.choices = [(0, "-- Без шаблона --")] + [
        (row.id, f"{row.name} ({row.code})") for row in templates
    ]

    if form.validate_on_submit():
        try:
            payload = {
                "code": form.code.data,
                "version": form.version.data,
                "name": form.name.data,
                **ruleset_payload_from_request(
                    request,
                    base_config=build_default_ruleset_payload(),
                    base_model_settings={},
                ),
                "active_start_pack_template_id": form.active_start_pack_template_id.data or None,
                "is_active": bool(form.is_active.data),
                "is_builtin": bool(form.is_builtin.data),
            }
            create_ruleset_for_admin(payload)
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
        ruleset_sections=ruleset_sections(),
        ruleset_values=_ruleset_values_for_page({}, {}),
        strategy_matrix=strategy_profile_matrix(),
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/model/<int:ruleset_id>/edit", methods=["GET", "POST"])
@pages_bp.route("/admin/rulesets/<int:ruleset_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_ruleset_edit_page(ruleset_id: int):
    try:
        row = get_ruleset_for_admin(ruleset_id)
    except ValueError:
        return _admin_missing_redirect("Набор правил не найден.", "pages.settings_model_page")
    form = RulesetForm()
    templates = list_start_pack_templates_for_admin(include_inactive=True)
    form.active_start_pack_template_id.choices = [(0, "-- Без шаблона --")] + [
        (it.id, f"{it.name} ({it.code})") for it in templates
    ]

    if request.method == "GET":
        form.code.data = row.code
        form.version.data = row.version
        form.name.data = row.name
        form.active_start_pack_template_id.data = row.active_start_pack_template_id or 0
        form.is_active.data = bool(row.is_active)
        form.is_builtin.data = bool(row.is_builtin)

    if form.validate_on_submit():
        try:
            update_ruleset_for_admin(
                row,
                {
                    "name": form.name.data,
                    **ruleset_payload_from_request(
                        request,
                        base_config=row.config_json or {},
                        base_model_settings=row.model_settings_json or {},
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
        ruleset_sections=ruleset_sections(),
        ruleset_values=_ruleset_values_for_page(row.config_json or {}, row.model_settings_json or {}),
        strategy_matrix=strategy_profile_matrix(),
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.post("/settings/model/<int:ruleset_id>/copy")
@pages_bp.post("/admin/rulesets/<int:ruleset_id>/copy")
@login_required
@role_required("admin")
def settings_ruleset_copy_action(ruleset_id: int):
    try:
        row = get_ruleset_for_admin(ruleset_id)
    except ValueError:
        return _admin_missing_redirect("Набор правил не найден.", "pages.settings_model_page")
    form = RulesetCopyForm()
    if form.validate_on_submit():
        try:
            copy_ruleset_for_admin(row, name=form.name.data, code=form.code.data)
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
    try:
        row = get_ruleset_for_admin(ruleset_id)
    except ValueError:
        return _admin_missing_redirect("Набор правил не найден.", "pages.settings_model_page")
    activate_ruleset_for_admin(row)
    flash("Набор правил активирован", "success")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.post("/settings/model/<int:ruleset_id>/deactivate")
@pages_bp.post("/admin/rulesets/<int:ruleset_id>/deactivate")
@login_required
@role_required("admin")
def settings_ruleset_deactivate_action(ruleset_id: int):
    try:
        row = get_ruleset_for_admin(ruleset_id)
    except ValueError:
        return _admin_missing_redirect("Набор правил не найден.", "pages.settings_model_page")
    try:
        deactivate_ruleset_for_admin(row)
        flash("Набор правил деактивирован", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("pages.settings_model_page"))


@pages_bp.get("/settings/start-packs")
@pages_bp.get("/admin/start-packs")
@login_required
@role_required("admin")
def settings_start_packs_page():
    templates = list_start_pack_templates_for_admin(include_inactive=True)
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
            create_start_pack_template_for_admin(
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

    object_types = list_object_types_for_admin(include_inactive=False)
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
    try:
        row = get_start_pack_template_for_admin(template_id)
    except ValueError:
        return _admin_missing_redirect(
            "Шаблон стартового пакета не найден.",
            "pages.settings_start_packs_page",
        )
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
            update_start_pack_template_for_admin(
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

    object_types = list_object_types_for_admin(include_inactive=True)
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
    try:
        row = get_start_pack_template_for_admin(template_id)
    except ValueError:
        return _admin_missing_redirect(
            "Шаблон стартового пакета не найден.",
            "pages.settings_start_packs_page",
        )
    deactivate_start_pack_template_for_admin(row)
    flash("Шаблон стартового пакета деактивирован", "success")
    return redirect(url_for("pages.settings_start_packs_page"))


@pages_bp.get("/settings/object-types")
@pages_bp.get("/admin/object-types")
@login_required
@role_required("admin")
def settings_object_types_page():
    rows = list_object_types_for_admin(include_inactive=True)
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
    _prepare_object_type_form(form)

    if form.validate_on_submit():
        try:
            payload = object_type_payload_from_request(
                request,
                category=form.category.data,
                subtype=form.subtype.data or "",
            )
            create_object_type_for_admin(
                {
                    "code": form.code.data,
                    "name": form.name.data,
                    "category": form.category.data,
                    "subtype": form.subtype.data,
                    "description": form.description.data,
                    **payload,
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
        **_object_type_editor_state(form),
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.route("/settings/object-types/<int:object_type_id>/edit", methods=["GET", "POST"])
@pages_bp.route("/admin/object-types/<int:object_type_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings_object_type_edit_page(object_type_id: int):
    try:
        row = get_object_type_for_admin(object_type_id)
    except ValueError:
        return _admin_missing_redirect("Тип объекта не найден.", "pages.settings_object_types_page")
    form = ObjectTypeForm()
    _prepare_object_type_form(form, row=row)

    if request.method == "GET":
        form.code.data = row.code
        form.name.data = row.name
        form.category.data = row.category
        form.subtype.data = row.subtype
        form.description.data = row.description
        form.is_active.data = bool(row.is_active)
        _prepare_object_type_form(form, row=row)

    if form.validate_on_submit():
        try:
            payload = object_type_payload_from_request(
                request,
                category=form.category.data,
                subtype=form.subtype.data or "",
                base_defaults=row.default_parameters_json or {},
                base_rules=row.rules_json or {},
            )
            update_object_type_for_admin(
                row,
                {
                    "name": form.name.data,
                    "category": form.category.data,
                    "subtype": form.subtype.data,
                    "description": form.description.data,
                    **payload,
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
        **_object_type_editor_state(form, row=row),
        admin_links=admin_links(),
        **ctx,
    )


@pages_bp.post("/settings/object-types/<int:object_type_id>/deactivate")
@pages_bp.post("/admin/object-types/<int:object_type_id>/deactivate")
@login_required
@role_required("admin")
def settings_object_type_deactivate_action(object_type_id: int):
    try:
        row = get_object_type_for_admin(object_type_id)
    except ValueError:
        return _admin_missing_redirect("Тип объекта не найден.", "pages.settings_object_types_page")
    deactivate_object_type_for_admin(row)
    flash("Тип объекта деактивирован", "success")
    return redirect(url_for("pages.settings_object_types_page"))
