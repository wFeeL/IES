from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

from flask import url_for

from ..models import GameSession
from ..services.analysis_context import session_analysis_settings, summarize_forecasts
from ..services.navigation import build_breadcrumbs, safe_back_url
from ..services.stale import stale_summary_for_session


def parse_json(raw: str, *, field_name: str, default: Any) -> Any:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Некорректный JSON в поле {field_name}: {exc}") from exc


def breadcrumbs(
    items: Iterable[Tuple[str, str | None, Dict[str, Any] | None]],
) -> List[Dict[str, Any]]:
    return build_breadcrumbs(items)


def session_stale_ctx(session: GameSession) -> Dict[str, Any]:
    return {"stale_warning": stale_summary_for_session(session.id)}


def nav(
    *,
    breadcrumb_items: Iterable[Tuple[str, str | None, Dict[str, Any] | None]],
    fallback_endpoint: str,
    fallback_values: Dict[str, Any] | None = None,
    cancel_url: str | None = None,
) -> Dict[str, Any]:
    return {
        "breadcrumbs": breadcrumbs(breadcrumb_items),
        "back_url": safe_back_url(
            fallback_endpoint=fallback_endpoint,
            **(fallback_values or {}),
        ),
        "cancel_url": cancel_url,
    }


def session_analysis_view(session: GameSession) -> Dict[str, Any]:
    settings = session_analysis_settings(session)
    selected_forecast = session.selected_forecast
    return {
        "analysis_settings": settings,
        "analysis_mode_label": "С прогнозом"
        if settings["analysis_mode"] == "forecast"
        else "Без прогноза",
        "forecast_summary": (
            summarize_forecasts([selected_forecast])
            if selected_forecast is not None
            else summarize_forecasts(session.forecasts)
        ),
        "corridor_summary": settings["corridor_summary"],
    }


def admin_links() -> List[Dict[str, str]]:
    return [
        {"label": "Наборы правил", "url": url_for("pages.settings_model_page")},
        {"label": "Стартовые пакеты", "url": url_for("pages.settings_start_packs_page")},
        {"label": "Типы объектов", "url": url_for("pages.settings_object_types_page")},
    ]
