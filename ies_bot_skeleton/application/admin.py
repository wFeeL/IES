from __future__ import annotations

from typing import Any, Dict, List

from ..web.models import GameSession, ObjectInstance, ObjectType, Ruleset, StartPackTemplate
from ..web.services.object_type_admin import (
    create_object_type,
    deactivate_object_type,
    get_object_type_or_error,
    list_object_types,
    update_object_type,
)
from ..web.services.ruleset import build_default_ruleset_config
from ..web.services.ruleset_admin import (
    activate_ruleset,
    copy_ruleset,
    create_ruleset,
    deactivate_ruleset,
    get_ruleset_or_error,
    list_rulesets,
    update_ruleset,
)
from ..web.services.start_pack import (
    apply_start_pack_template_to_session,
    create_start_pack_template,
    deactivate_start_pack_template,
    get_start_pack_template_or_error,
    list_start_pack_templates,
    update_start_pack_template,
)


def list_rulesets_for_admin() -> List[Ruleset]:
    return list_rulesets()


def get_ruleset_for_admin(ruleset_id: int) -> Ruleset:
    return get_ruleset_or_error(ruleset_id)


def create_ruleset_for_admin(payload: Dict[str, Any]) -> Ruleset:
    return create_ruleset(payload)


def update_ruleset_for_admin(row: Ruleset, payload: Dict[str, Any]) -> Ruleset:
    return update_ruleset(row, payload)


def copy_ruleset_for_admin(
    source: Ruleset, *, name: str | None = None, code: str | None = None
) -> Ruleset:
    return copy_ruleset(source, name=name, code=code)


def activate_ruleset_for_admin(row: Ruleset) -> Ruleset:
    return activate_ruleset(row)


def deactivate_ruleset_for_admin(row: Ruleset) -> Ruleset:
    return deactivate_ruleset(row)


def build_default_ruleset_payload() -> Dict[str, Any]:
    return build_default_ruleset_config()


def list_start_pack_templates_for_admin(
    *, include_inactive: bool = False
) -> List[StartPackTemplate]:
    return list_start_pack_templates(include_inactive=include_inactive)


def get_start_pack_template_for_admin(template_id: int) -> StartPackTemplate:
    return get_start_pack_template_or_error(template_id)


def create_start_pack_template_for_admin(
    *,
    code: str,
    name: str,
    description: str,
    is_active: bool,
    is_builtin: bool,
    items_payload: Any,
) -> StartPackTemplate:
    return create_start_pack_template(
        code=code,
        name=name,
        description=description,
        is_active=is_active,
        is_builtin=is_builtin,
        items_payload=items_payload,
    )


def update_start_pack_template_for_admin(
    *, row: StartPackTemplate, payload: Dict[str, Any]
) -> StartPackTemplate:
    return update_start_pack_template(row=row, payload=payload)


def deactivate_start_pack_template_for_admin(row: StartPackTemplate) -> StartPackTemplate:
    return deactivate_start_pack_template(row)


def apply_start_pack_to_session(
    *, session: GameSession, template_id: int | None = None
) -> List[ObjectInstance]:
    return apply_start_pack_template_to_session(session=session, template_id=template_id)


def list_object_types_for_admin(*, include_inactive: bool = False) -> List[ObjectType]:
    return list_object_types(include_inactive=include_inactive)


def get_object_type_for_admin(object_type_id: int) -> ObjectType:
    return get_object_type_or_error(object_type_id)


def create_object_type_for_admin(payload: Dict[str, Any]) -> ObjectType:
    return create_object_type(payload)


def update_object_type_for_admin(row: ObjectType, payload: Dict[str, Any]) -> ObjectType:
    return update_object_type(row, payload)


def deactivate_object_type_for_admin(row: ObjectType) -> ObjectType:
    return deactivate_object_type(row)
