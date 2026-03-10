from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from ..extensions import db
from ..models import Ruleset, StartPackTemplate
from .stale import mark_stale_for_ruleset


def list_rulesets() -> List[Ruleset]:
    return db.session.query(Ruleset).order_by(Ruleset.created_at.desc(), Ruleset.id.desc()).all()


def get_ruleset_or_error(ruleset_id: int) -> Ruleset:
    row = db.session.get(Ruleset, int(ruleset_id))
    if row is None:
        raise ValueError(f"Ruleset {ruleset_id} not found")
    return row


def _as_dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} должен быть объектом")
    return dict(value)


def _normalize_template_id(raw_value: Any) -> int | None:
    if raw_value in (None, "", 0, "0"):
        return None
    template_id = int(raw_value)
    template = db.session.get(StartPackTemplate, template_id)
    if template is None:
        raise ValueError(f"Start pack template {template_id} not found")
    return template_id


def _next_version_for_code(code: str) -> str:
    rows = db.session.query(Ruleset).filter_by(code=code).all()
    if not rows:
        return "1"
    numeric = []
    for row in rows:
        try:
            numeric.append(int(str(row.version)))
        except ValueError:
            continue
    if numeric:
        return str(max(numeric) + 1)
    return str(len(rows) + 1)


def _deactivate_other_rulesets(active_id: int) -> None:
    rows = db.session.query(Ruleset).filter(Ruleset.id != int(active_id), Ruleset.is_active.is_(True)).all()
    for row in rows:
        row.is_active = False
        db.session.add(row)


def create_ruleset(payload: Dict[str, Any]) -> Ruleset:
    code = str(payload.get("code", "")).strip()
    name = str(payload.get("name", "")).strip()
    version = str(payload.get("version", "")).strip() or _next_version_for_code(code)
    if not code or not name:
        raise ValueError("code и name обязательны")

    existing = db.session.query(Ruleset).filter_by(code=code, version=version).first()
    if existing is not None:
        raise ValueError(f"Ruleset {code}:{version} already exists")

    is_active = bool(payload.get("is_active", False))
    row = Ruleset(
        code=code,
        version=version,
        name=name,
        config_json=_as_dict(payload.get("config_json"), field_name="config_json"),
        model_settings_json=_as_dict(
            payload.get("model_settings"),
            field_name="model_settings",
        ),
        active_start_pack_template_id=_normalize_template_id(
            payload.get("active_start_pack_template_id")
        ),
        is_builtin=bool(payload.get("is_builtin", False)),
        is_active=is_active,
    )
    db.session.add(row)
    db.session.flush()

    if is_active:
        _deactivate_other_rulesets(row.id)

    db.session.add(row)
    db.session.commit()
    return row


def copy_ruleset(source: Ruleset, *, name: str | None = None, code: str | None = None) -> Ruleset:
    source_code = str(code or source.code).strip()
    if not source_code:
        raise ValueError("code не может быть пустым")

    row = Ruleset(
        code=source_code,
        version=_next_version_for_code(source_code),
        name=str(name or f"{source.name} copy").strip(),
        config_json=deepcopy(dict(source.config_json or {})),
        model_settings_json=deepcopy(dict(source.model_settings_json or {})),
        active_start_pack_template_id=source.active_start_pack_template_id,
        is_builtin=False,
        is_active=False,
    )
    if not row.name:
        raise ValueError("name не может быть пустым")

    db.session.add(row)
    db.session.commit()
    return row


def update_ruleset(row: Ruleset, payload: Dict[str, Any]) -> Ruleset:
    stale_reasons: List[str] = []

    if "name" in payload:
        value = str(payload.get("name") or "").strip()
        if not value:
            raise ValueError("name не может быть пустым")
        row.name = value

    if "config_json" in payload:
        row.config_json = _as_dict(payload.get("config_json"), field_name="config_json")
        stale_reasons.append("ruleset_config_changed")

    if "model_settings" in payload:
        row.model_settings_json = _as_dict(
            payload.get("model_settings"),
            field_name="model_settings",
        )
        stale_reasons.append("ruleset_model_settings_changed")

    if "active_start_pack_template_id" in payload:
        row.active_start_pack_template_id = _normalize_template_id(
            payload.get("active_start_pack_template_id")
        )
        stale_reasons.append("ruleset_start_pack_changed")

    if "is_active" in payload:
        row.is_active = bool(payload.get("is_active"))

    db.session.add(row)
    db.session.flush()

    if row.is_active:
        _deactivate_other_rulesets(row.id)

    db.session.commit()

    if stale_reasons:
        mark_stale_for_ruleset(row.id, reason=";".join(stale_reasons))

    return row


def activate_ruleset(row: Ruleset) -> Ruleset:
    row.is_active = True
    _deactivate_other_rulesets(row.id)
    db.session.add(row)
    db.session.commit()
    return row


def deactivate_ruleset(row: Ruleset) -> Ruleset:
    active_count = db.session.query(Ruleset).filter_by(is_active=True).count()
    if row.is_active and active_count <= 1:
        raise ValueError("Нельзя деактивировать последний активный ruleset")

    row.is_active = False
    db.session.add(row)
    db.session.commit()
    return row
