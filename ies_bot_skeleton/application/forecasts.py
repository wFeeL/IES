from __future__ import annotations

from typing import Any, Dict

from ..web.models import Forecast
from ..web.services.forecast_service import (
    bundled_forecast_summary as bundled_forecast_summary_service,
    load_bundled_forecast_pack,
    parse_and_store_forecast,
    summarize_forecast,
)


def load_bundled_forecasts() -> Dict[str, Dict[str, Dict[int, float]]]:
    return load_bundled_forecast_pack()


def bundled_forecast_summary_payload() -> Dict[str, Any]:
    return bundled_forecast_summary_service()


bundled_forecast_summary = bundled_forecast_summary_payload


def parse_uploaded_forecast(**kwargs: Any):
    return parse_and_store_forecast(**kwargs)


def summarize_stored_forecast(forecast: Forecast) -> Dict[str, Any]:
    return summarize_forecast(forecast)
