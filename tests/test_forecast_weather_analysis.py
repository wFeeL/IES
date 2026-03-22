from __future__ import annotations

import io

import pytest

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import GameSession
from ies_bot_skeleton.web.services.forecast_service import parse_and_store_forecast, summarize_forecast
from tests.web_helpers import create_session, login


def _session(title: str = "Weather analysis") -> GameSession:
    session = GameSession(title=title, ruleset_id=1, selected_strategy="balanced")
    db.session.add(session)
    db.session.commit()
    return session


def _full_nto_csv() -> bytes:
    rows = [
        "tick,wind_from,wind_to,sun_east,sun_west,hospital,factory,house_a,house_b",
        "0,4,6,10,20,5,7,3,4",
        "1,8,8,0,15,6,8,4,5",
        "2,2,4,8,0,4,6,2,3",
        "3,3,3,0,0,4,5,2,2",
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")


def _canonical_csv() -> bytes:
    rows = ["tick,wind,illumination,house,office,factory,market_price"]
    for tick in range(4):
        rows.append(
            f"{tick},{3 + tick},{0.4 + tick * 0.1:.2f},{9 + tick},{6 + (tick % 2)},{5 + tick},{10 + tick}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _partial_csv() -> bytes:
    rows = [
        "tick,wind_from,wind_to,sun_east,factory",
        "0,4,6,10,7",
        "1,9,9,0,8",
        "2,2,3,5,6",
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_summarize_forecast_returns_full_nto_weather_analysis(app):
    session = _session("Full NTO weather")
    forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="NTO full",
        source_file="nto_full.csv",
        content=_full_nto_csv(),
    )

    summary = summarize_forecast(forecast)
    weather = summary["weather_analysis"]

    assert "weather_analysis" in summary
    assert weather["mode"] == "full_nto_2024"
    assert weather["availability"]["has_wind_range"] is True
    assert weather["availability"]["has_solar_east_west"] is True
    assert weather["availability"]["has_category_breakdown"] is True
    assert weather["charts"]["solar_models"]["available"] is True
    assert weather["decision_support"]["cards"]

    series = weather["series"]
    assert series["solar_east_gen"][0] == pytest.approx(11.91, rel=1e-4)
    assert series["solar_west_gen"][0] == pytest.approx(21.01, rel=1e-4)
    assert series["solar_simple"][0] == pytest.approx(16.46, rel=1e-4)
    assert series["solar_improved"][1] == pytest.approx(16.46, rel=1e-4)
    assert series["wind_avg"][0] == pytest.approx(5.0, rel=1e-4)
    assert series["wind_gen"][0] == pytest.approx(6.6667, rel=1e-4)
    assert series["wind_gen"][1] == pytest.approx(0.0, rel=1e-4)
    assert series["total_consumption"][0] == pytest.approx(19.0, rel=1e-4)
    assert series["total_generation"][0] == pytest.approx(23.1267, rel=1e-4)
    assert series["balance"][0] == pytest.approx(4.1267, rel=1e-4)
    assert weather["kpis"]["wind_off_count"] == 1
    assert weather["kpis"]["surplus_count"] >= 1
    assert weather["series"]["category_consumption"]["hospital"][0] == pytest.approx(5.0, rel=1e-4)


def test_summarize_forecast_returns_canonical_fallback_weather_analysis(app):
    session = _session("Canonical fallback")
    forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="Canonical weather",
        source_file="canonical.csv",
        content=_canonical_csv(),
    )

    summary = summarize_forecast(forecast)
    weather = summary["weather_analysis"]
    series = weather["series"]

    assert weather["mode"] == "canonical_fallback"
    assert weather["availability"]["has_wind_range"] is False
    assert weather["availability"]["has_solar_east_west"] is False
    assert weather["charts"]["solar_models"]["available"] is False
    assert "sun_east" in weather["charts"]["solar_models"]["reason"]
    assert weather["decision_support"]["headline"]
    assert series["solar_improved"][0] == pytest.approx(3.174, rel=1e-4)
    assert series["wind_avg"][0] == pytest.approx(3.0, rel=1e-4)
    assert series["wind_gen"][0] == pytest.approx(4.0, rel=1e-4)
    assert series["total_consumption"][0] == pytest.approx(20.0, rel=1e-4)
    assert series["total_generation"][0] == pytest.approx(7.174, rel=1e-4)
    assert series["balance"][0] == pytest.approx(-12.826, rel=1e-4)
    assert series["balance_min"][0] == pytest.approx(-14.781, rel=1e-4)
    assert series["balance_max"][0] == pytest.approx(-10.871, rel=1e-4)
    assert weather["series"]["category_consumption"]["office"][0] == pytest.approx(6.0, rel=1e-4)


def test_summarize_forecast_returns_partial_weather_analysis_without_crashing(app):
    session = _session("Partial weather")
    forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="Partial weather",
        source_file="partial.csv",
        content=_partial_csv(),
    )

    summary = summarize_forecast(forecast)
    weather = summary["weather_analysis"]

    assert weather["mode"] == "partial"
    assert weather["availability"]["has_solar_east_west"] is False
    assert weather["availability"]["has_wind_range"] is True
    assert weather["series"]["solar_improved"][0] == pytest.approx(11.91, rel=1e-4)
    assert weather["series"]["total_consumption"][0] == pytest.approx(7.0, rel=1e-4)
    assert weather["series"]["wind_gen"][1] == pytest.approx(0.0, rel=1e-4)
    assert weather["charts"]["solar_models"]["available"] is False
    assert weather["insights"]


def test_get_forecast_endpoint_returns_weather_analysis_block(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Weather API")

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Weather API forecast",
            "file": (io.BytesIO(_canonical_csv()), "weather.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    forecast_id = int(upload.get_json()["item"]["id"])

    resp = client.get(f"/api/forecast/{forecast_id}")
    assert resp.status_code == 200
    weather = resp.get_json()["item"]["summary"]["weather_analysis"]
    assert weather["mode"] == "canonical_fallback"
    assert "generation_vs_consumption" in weather["charts"]
    assert "main_stats" in weather["tables"]


def test_forecast_page_and_script_expose_weather_analysis_ui(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Weather page")

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Weather page forecast",
            "file": (io.BytesIO(_canonical_csv()), "weather_page.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    page = client.get(f"/forecast/{session_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Анализ прогноза погоды" in html
    assert 'id="activeForecastWeatherAnalysis"' in html
    assert "activeWeatherAnalysis" in html

    script = client.get("/static/js/analysis/forecast_center.js")
    assert script.status_code == 200
    js = script.get_data(as_text=True)
    assert "function renderWeatherAnalysis(target, analysis)" in js
    assert "function renderWeatherOverview(target, analysis)" in js
    assert "function renderWeatherKpis(target, analysis)" in js
    assert "function renderWeatherDecisionSupport(target, analysis)" in js
    assert "function renderWeatherCharts(target, analysis)" in js
    assert "function renderWeatherTables(target, analysis)" in js
    assert "function renderWeatherInsights(target, analysis)" in js
