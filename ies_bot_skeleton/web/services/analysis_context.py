from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import Forecast, GameSession
from .forecast_service import summarize_forecast_for_session

NO_FORECAST_SUMMARY = {
    "forecast_id": None,
    "source_kind": "missing_forecast",
    "name": "Прогноз не выбран",
    "source_file": "",
    "count": 0,
    "tick_from": None,
    "tick_to": None,
    "avg_wind": None,
    "avg_illumination": None,
    "avg_market_price": None,
    "factors_keys": [],
    "profiles_keys": [],
    "load_series": [],
    "load_series_display": [],
    "load_series_raw": [],
    "raw_csv_columns": [],
    "used_raw_columns": [],
    "unsupported_raw_columns": [],
    "column_mapping_rows": [],
    "mapped_raw_columns": [],
    "mapped_raw_stats_display": [],
    "mapped_tick_range_label": "—",
    "consumer_averages": {},
    "series_stats": {},
    "series_stats_display": [],
    "internal_canonical_load_series": [],
    "internal_canonical_series_stats": {},
    "column_display_labels": {},
    "resolved_column_map": {},
    "warnings": ["Оценка лотов заблокирована: сначала загрузите прогноз 2026."],
    "text": "Нет активного прогноза. Загрузите реальный CSV-прогноз, после этого станет доступна оценка лотов.",
    "compatibility_report": {
        "is_compatible": False,
        "blocking_reasons": ["Нет активного пользовательского прогноза для этой сессии."],
        "covered_types": [],
        "partial_types": [],
        "missing_types": [],
        "rows": [],
        "unused_columns": [],
        "normalization_map": {},
    },
    "is_compatible": False,
    "incompatibility_reason": "Нет активного пользовательского прогноза для этой сессии.",
    "quality": {
        "warnings": ["Оценка заблокирована до загрузки прогноза."],
        "problem_columns": [],
        "empty_columns": [],
        "text": "Оценка заблокирована до загрузки реального прогноза.",
    },
    "weather_analysis": {
        "mode": "missing_forecast",
        "availability": {
            "has_wind_range": False,
            "has_solar_east_west": False,
            "has_category_breakdown": False,
        },
        "kpis": {},
        "series": {"tick": []},
        "charts": {},
        "decision_support": {
            "headline": "Сначала загрузите прогноз.",
            "cards": [],
        },
        "tables": {"main_stats": [], "generator_stats": []},
        "insights": ["Без прогноза pre-auction valuation выключен."],
    },
}


def session_analysis_settings(session: GameSession) -> Dict[str, Any]:
    start_budget = float(session.budget_total or 0.0)
    return {
        "selected_strategy": "unified",
        "selected_forecast_id": int(session.selected_forecast_id) if session.selected_forecast_id else None,
        "start_budget": start_budget,
        "budget_total": start_budget,
        "allpay_spent": float(getattr(session, "allpay_spent", 0.0) or 0.0),
        "analysis_mode": "unified_optimizer",
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
    if session.selected_forecast is not None and int(session.selected_forecast.session_id or 0) == int(session.id):
        return session.selected_forecast
    return None



def update_session_analysis_settings(session: GameSession, payload: Dict[str, Any]) -> Dict[str, Any]:
    selected_forecast_id = payload.get("selected_forecast_id", session.selected_forecast_id)
    if selected_forecast_id not in (None, "", 0, "0"):
        selected = resolve_forecast_for_session(session, forecast_id=selected_forecast_id)
        session.selected_forecast_id = selected.id if selected is not None else None
    else:
        session.selected_forecast_id = None

    if "allpay_spent" in payload:
        session.allpay_spent = max(0.0, float(payload.get("allpay_spent", 0.0) or 0.0))

    if "selected_strategy" in payload:
        session.selected_strategy = "unified"

    return session_analysis_settings(session)



def resolve_analysis_context(
    session: GameSession,
    *,
    forecast_id: Any = None,
) -> Dict[str, Any]:
    selected_forecast = resolve_forecast_for_session(session, forecast_id=forecast_id)
    if selected_forecast is not None:
        summary = dict(summarize_forecast_for_session(session=session, forecast=selected_forecast))
        source = "selected_forecast"
        source_label = "Пользовательский прогноз"
        forecast_name = selected_forecast.name
        forecast_pk = int(selected_forecast.id)
    else:
        summary = dict(NO_FORECAST_SUMMARY)
        source = "missing_forecast"
        source_label = "Прогноз не выбран"
        forecast_name = summary.get("name") or "Прогноз не выбран"
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
