from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required

from ...application.admin import (
    activate_ruleset_for_admin,
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
    list_rulesets_for_admin,
    list_start_pack_templates_for_admin,
    update_object_type_for_admin,
    update_ruleset_for_admin,
    update_start_pack_template_for_admin,
)
from ..services.auth import is_admin, role_required
from .api_support import json_payload
from .shared import api_bp


@api_bp.get("/rulesets")
@login_required
def rulesets_list_endpoint():
    rows = list_rulesets_for_admin()
    return jsonify({"ok": True, "items": [row.to_dict() for row in rows]})


@api_bp.post("/rulesets")
@login_required
@role_required("admin")
def rulesets_create_endpoint():
    payload = json_payload()
    row = create_ruleset_for_admin(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/rulesets/<int:ruleset_id>")
@login_required
@role_required("admin")
def rulesets_update_endpoint(ruleset_id: int):
    row = get_ruleset_for_admin(ruleset_id)
    payload = json_payload()
    updated = update_ruleset_for_admin(row, payload)
    return jsonify({"ok": True, "item": updated.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/copy")
@login_required
@role_required("admin")
def rulesets_copy_endpoint(ruleset_id: int):
    row = get_ruleset_for_admin(ruleset_id)
    payload = json_payload()
    copied = copy_ruleset_for_admin(row, name=payload.get("name"), code=payload.get("code"))
    return jsonify({"ok": True, "item": copied.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/activate")
@login_required
@role_required("admin")
def rulesets_activate_endpoint(ruleset_id: int):
    row = get_ruleset_for_admin(ruleset_id)
    activate_ruleset_for_admin(row)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.post("/rulesets/<int:ruleset_id>/deactivate")
@login_required
@role_required("admin")
def rulesets_deactivate_endpoint(ruleset_id: int):
    row = get_ruleset_for_admin(ruleset_id)
    deactivate_ruleset_for_admin(row)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.get("/start-pack-templates")
@login_required
def start_pack_templates_list_endpoint():
    include_inactive = bool(request.args.get("include_inactive", type=int))
    if include_inactive and not is_admin():
        include_inactive = False
    rows = list_start_pack_templates_for_admin(include_inactive=include_inactive)
    return jsonify({"ok": True, "items": [row.to_dict(include_items=True) for row in rows]})


@api_bp.get("/start-pack-templates/<int:template_id>")
@login_required
def start_pack_templates_get_endpoint(template_id: int):
    row = get_start_pack_template_for_admin(template_id)
    return jsonify({"ok": True, "item": row.to_dict(include_items=True)})


@api_bp.post("/start-pack-templates")
@login_required
@role_required("admin")
def start_pack_templates_create_endpoint():
    payload = json_payload()
    row = create_start_pack_template_for_admin(
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
    row = get_start_pack_template_for_admin(template_id)
    payload = json_payload()
    out = update_start_pack_template_for_admin(row=row, payload=payload)
    return jsonify({"ok": True, "item": out.to_dict(include_items=True)})


@api_bp.delete("/start-pack-templates/<int:template_id>")
@login_required
@role_required("admin")
def start_pack_templates_delete_endpoint(template_id: int):
    row = get_start_pack_template_for_admin(template_id)
    deactivate_start_pack_template_for_admin(row)
    return jsonify({"ok": True})


@api_bp.post("/object-types")
@login_required
@role_required("admin")
def create_object_type():
    payload = json_payload()
    row = create_object_type_for_admin(payload)
    return jsonify({"ok": True, "item": row.to_dict()})


@api_bp.put("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def update_object_type(object_type_id: int):
    row = get_object_type_for_admin(object_type_id)
    payload = json_payload()
    out = update_object_type_for_admin(row, payload)
    return jsonify({"ok": True, "item": out.to_dict()})


@api_bp.delete("/object-types/<int:object_type_id>")
@login_required
@role_required("admin")
def delete_object_type(object_type_id: int):
    row = get_object_type_for_admin(object_type_id)
    deactivate_object_type_for_admin(row)
    return jsonify({"ok": True})
