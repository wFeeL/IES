from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required

from ..services.auth import is_admin, role_required
from ..services.legacy_import import import_legacy_data
from ..services.object_type_admin import (
    create_object_type as create_object_type_service,
)
from ..services.object_type_admin import (
    deactivate_object_type,
    get_object_type_or_error,
    list_object_types as list_object_types_service,
    update_object_type as update_object_type_service,
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
from .api_support import get_session_or_404, json_payload
from .shared import api_bp


@api_bp.get("/rulesets")
@login_required
def rulesets_list_endpoint():
    rows = list_rulesets()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/rulesets")
@login_required
@role_required("admin")
def rulesets_create_endpoint():
    payload = json_payload()
    row = create_ruleset(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/rulesets/<int:ruleset_id>")
@login_required
@role_required("admin")
def rulesets_update_endpoint(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    payload = json_payload()
    updated = update_ruleset(row, payload)
    return jsonify({"ok": True, "item": updated.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/copy")
@login_required
@role_required("admin")
def rulesets_copy_endpoint(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    payload = json_payload()
    copied = copy_ruleset(row, name=payload.get("name"), code=payload.get("code"))
    return jsonify({"ok": True, "item": copied.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/activate")
@login_required
@role_required("admin")
def rulesets_activate_endpoint(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    activate_ruleset(row)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/deactivate")
@login_required
@role_required("admin")
def rulesets_deactivate_endpoint(ruleset_id: int):
    row = get_ruleset_or_error(ruleset_id)
    deactivate_ruleset(row)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/start-pack-templates")
@login_required
def start_pack_templates_list_endpoint():
    include_inactive = bool(request.args.get("include_inactive", type=int))
    if include_inactive and not is_admin():
        include_inactive = False
    rows = list_start_pack_templates(include_inactive=include_inactive)
    return jsonify({"ok": True, "items": [row.to_dict(include_items=True) for row in rows]})


@api_bp.get("/start-pack-templates/<int:template_id>")
@login_required
def start_pack_templates_get_endpoint(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    return jsonify({"ok": True, "item": row.to_dict(include_items=True)})


@api_bp.post("/start-pack-templates")
@login_required
@role_required("admin")
def start_pack_templates_create_endpoint():
    payload = json_payload()
    row = create_start_pack_template(
        code=str(payload.get("code", "")),
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        is_active=bool(payload.get("is_active", True)),
        is_builtin=bool(payload.get("is_builtin", False)),
        items_payload=payload.get("items") or [],
    )
    return jsonify({"ok": True, "item": row.to_dict(include_items=True)})


@api_bp.put("/start-pack-templates/<int:template_id>")
@login_required
@role_required("admin")
def start_pack_templates_update_endpoint(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    payload = json_payload()
    out = update_start_pack_template(row=row, payload=payload)
    return jsonify({"ok": True, "item": out.to_dict(include_items=True)})


@api_bp.delete("/start-pack-templates/<int:template_id>")
@login_required
@role_required("admin")
def start_pack_templates_delete_endpoint(template_id: int):
    row = get_start_pack_template_or_error(template_id)
    deactivate_start_pack_template(row)
    return jsonify({"ok": True})


@api_bp.post("/object-types")
@login_required
@role_required("admin")
def create_object_type():
    payload = json_payload()
    row = create_object_type_service(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def update_object_type(object_type_id: int):
    row = get_object_type_or_error(object_type_id)
    payload = json_payload()
    out = update_object_type_service(row, payload)
    return jsonify({"ok": True, "item": out.to_dict()})


@api_bp.delete("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def delete_object_type(object_type_id: int):
    row = get_object_type_or_error(object_type_id)
    deactivate_object_type(row)
    return jsonify({"ok": True})


@api_bp.post("/legacy/import")
@login_required
@role_required("admin")
def legacy_import_endpoint():
    payload = json_payload()
    session_id = int(payload.get("session_id", 0) or 0)
    if session_id <= 0:
        raise ValueError("session_id обязателен")
    get_session_or_404(session_id)
    report = import_legacy_data(session_id=session_id)
    return jsonify({"ok": True, "item": report})
