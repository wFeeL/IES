from __future__ import annotations

from typing import Any, Dict

from ..web.models import GameSession
from ..web.services.analysis_context import (
    resolve_analysis_context,
    session_analysis_settings,
    update_session_analysis_settings,
)
from ..web.services.corridor import ruleset_default_corridor_settings


def session_analysis_settings_for_session(session: GameSession) -> Dict[str, Any]:
    return session_analysis_settings(session)


def resolve_session_analysis_context(
    session: GameSession,
    *,
    requested_mode: Any = None,
    forecast_id: Any = None,
    corridor_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return resolve_analysis_context(
        session,
        requested_mode=requested_mode,
        forecast_id=forecast_id,
        corridor_override=corridor_override,
    )


def update_analysis_settings_for_session(
    session: GameSession, payload: Dict[str, Any]
) -> Dict[str, Any]:
    return update_session_analysis_settings(session, payload)


def default_corridor_settings_for_ruleset(config_json: Dict[str, Any] | None) -> Dict[str, Any]:
    return ruleset_default_corridor_settings(config_json or {})
