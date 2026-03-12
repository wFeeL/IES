from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from ..models import Forecast, GameSession
from .corridor import (
    build_corridor_summary,
    normalize_corridor_settings,
    ruleset_default_corridor_settings,
)
from .forecast_service import bundled_forecast_summary, summarize_forecast


VALID_ANALYSIS_MODES = {"no_forecast", "forecast"}


def normalize_analysis_mode(value: Any) -> str:
    mode = str(value or "no_forecast").strip().lower()
    if mode not in VALID_ANALYSIS_MODES:
        raise ValueError("analysis_mode должен быть одним из: no_forecast, forecast")
    return mode


def analysis_mode_label(mode: str) -> str:
    return "С прогнозом" if mode == "forecast" else "Без прогноза"


def session_analysis_settings(session: GameSession) -> Dict[str, Any]:
    fallback = ruleset_default_corridor_settings(
        session.ruleset.config_json if session.ruleset is not None else {}
    )
    settings = normalize_corridor_settings(session.corridor_settings_json or {}, fallback=fallback)
    return {
        "analysis_mode": normalize_analysis_mode(session.analysis_mode or "no_forecast"),
        "selected_forecast_id": session.selected_forecast_id,
        "corridor_settings": settings,
        "corridor_summary": build_corridor_summary(settings),
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


def summarize_forecasts(forecasts: Iterable[Forecast]) -> Dict[str, Any]:
    rows = list(forecasts)
    if not rows:
        return {
            "mode": "empty",
            "count": 0,
            "text": "Прогноз не выбран",
            "warnings": [],
            "load_series": [],
        }
    if len(rows) == 1:
        summary = summarize_forecast(rows[0])
        summary["mode"] = "single"
        return summary
    load_series = sorted(
        {
            key
            for forecast in rows
            for key in (summarize_forecast(forecast).get("load_series") or [])
        }
    )
    return {
        "mode": "merged",
        "count": len(rows),
        "text": f"Используется объединенный набор из {len(rows)} прогнозов.",
        "warnings": [],
        "load_series": load_series,
    }


def update_session_analysis_settings(session: GameSession, payload: Dict[str, Any]) -> Dict[str, Any]:
    current = session_analysis_settings(session)
    mode = normalize_analysis_mode(payload.get("analysis_mode", current["analysis_mode"]))
    settings = normalize_corridor_settings(
        payload.get("corridor_settings"),
        fallback=current["corridor_settings"],
    )

    selected_forecast = None
    selected_forecast_id = payload.get("selected_forecast_id", current["selected_forecast_id"])
    if selected_forecast_id not in (None, "", 0, "0"):
        selected_forecast = resolve_forecast_for_session(session, forecast_id=selected_forecast_id)
        selected_forecast_id = selected_forecast.id if selected_forecast is not None else None
    else:
        selected_forecast_id = None

    session.analysis_mode = mode
    session.selected_forecast_id = selected_forecast_id
    session.corridor_settings_json = dict(settings)
    return session_analysis_settings(session)


def resolve_analysis_context(
    session: GameSession,
    *,
    requested_mode: Any = None,
    forecast_id: Any = None,
    corridor_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    settings = session_analysis_settings(session)
    mode = normalize_analysis_mode(requested_mode or settings["analysis_mode"])
    corridor_settings = normalize_corridor_settings(
        corridor_override,
        fallback=settings["corridor_settings"],
    )

    selected_forecast = resolve_forecast_for_session(session, forecast_id=forecast_id)
    if mode == "forecast":
        if selected_forecast is not None:
            forecast_summary = summarize_forecast(selected_forecast)
            source = "selected_forecast"
            source_label = "Выбранный прогноз сессии"
        else:
            forecast_summary = bundled_forecast_summary()
            source = "bundled_forecast"
            source_label = "Встроенный базовый прогноз"
    else:
        forecast_summary = summarize_forecasts([])
        source = "manual_corridor"
        source_label = "Ручной коридор неопределенности"

    return {
        "mode": mode,
        "mode_label": analysis_mode_label(mode),
        "forecast": selected_forecast,
        "forecast_summary": forecast_summary,
        "corridor_settings": corridor_settings,
        "corridor_summary": build_corridor_summary(corridor_settings),
        "source": source,
        "source_label": source_label,
        "has_forecast_context": bool(selected_forecast is not None or mode == "forecast"),
        "uses_manual_corridor": mode == "no_forecast",
    }
