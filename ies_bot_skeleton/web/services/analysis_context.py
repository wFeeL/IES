from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import Forecast, GameSession
from .forecast_service import bundled_forecast_summary, summarize_forecast


def session_analysis_settings(session: GameSession) -> Dict[str, Any]:
    return {
        "selected_forecast_id": int(session.selected_forecast_id) if session.selected_forecast_id else None,
    }


def resolve_forecast_for_session(
    session: GameSession,
    *,
    forecast_id: Any = None,
) -> Optional[Forecast]:
    if forecast_id not in (None, "", 0, "0"):
        target_id = int(forecast_id)
        for forecast in session.forecasts:
            if int(forecast.id) == target_id:
                return forecast
        raise ValueError("Выбранный прогноз не принадлежит этой сессии")
    if session.selected_forecast is not None:
        return session.selected_forecast
    return None


def update_session_analysis_settings(session: GameSession, payload: Dict[str, Any]) -> Dict[str, Any]:
    selected_forecast_id = payload.get("selected_forecast_id", session.selected_forecast_id)
    if selected_forecast_id not in (None, "", 0, "0"):
        selected = resolve_forecast_for_session(session, forecast_id=selected_forecast_id)
        session.selected_forecast_id = selected.id if selected is not None else None
    else:
        session.selected_forecast_id = None
    return session_analysis_settings(session)


def resolve_analysis_context(
    session: GameSession,
    *,
    forecast_id: Any = None,
) -> Dict[str, Any]:
    selected_forecast = resolve_forecast_for_session(session, forecast_id=forecast_id)
    if selected_forecast is not None:
        summary = summarize_forecast(selected_forecast)
        source = "selected_forecast"
        source_label = "Пользовательский прогноз"
        forecast_name = selected_forecast.name
        forecast_pk = int(selected_forecast.id)
    else:
        summary = bundled_forecast_summary()
        source = "bundled_forecast"
        source_label = "Встроенный базовый прогноз"
        forecast_name = summary.get("name") or "Встроенный базовый прогноз"
        forecast_pk = None

    return {
        "forecast": selected_forecast,
        "forecast_summary": summary,
        "source": source,
        "source_label": source_label,
        "forecast_context": {
            "source": source,
            "source_label": source_label,
            "forecast_id": forecast_pk,
            "forecast_name": forecast_name,
            "tick_from": summary.get("tick_from"),
            "tick_to": summary.get("tick_to"),
            "periods_count": summary.get("count", 0),
        },
    }
