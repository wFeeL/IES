from __future__ import annotations

from typing import Any, Dict

from ..web.models import GameSession
from ..web.services.analysis_context import (
    resolve_analysis_context,
    session_analysis_settings,
    update_session_analysis_settings,
)


def session_analysis_settings_for_session(session: GameSession) -> Dict[str, Any]:
    return session_analysis_settings(session)


def resolve_session_analysis_context(
    session: GameSession,
    *,
    forecast_id: Any = None,
) -> Dict[str, Any]:
    return resolve_analysis_context(session, forecast_id=forecast_id)


def update_analysis_settings_for_session(
    session: GameSession, payload: Dict[str, Any]
) -> Dict[str, Any]:
    return update_session_analysis_settings(session, payload)
