from __future__ import annotations

import io

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import GameSession
from ies_bot_skeleton.web.services.forecast_service import (
    parse_and_store_forecast,
    summarize_forecast,
)
from tests.web_helpers import create_session, login


def _ru_weather_csv(rows_count: int = 99) -> bytes:
    rows = ["Ветер,Солнце,Больницы,Заводы,Офисы,Дома А,Дома Б"]
    for tick in range(rows_count):
        wind = 8.0 + (tick % 7) * 0.35
        sun = 0.0 if tick < 12 else min(14.5, (tick - 12) * 0.7)
        hospitals = 4.5 + (tick % 5) * 0.1
        factories = 5.2 + (tick % 6) * 0.15
        offices = 3.6 + (tick % 8) * 0.2
        house_a = 2.1 + (tick % 4) * 0.25
        house_b = 2.8 + (tick % 3) * 0.3
        rows.append(
            f"{wind:.4f},{sun:.4f},{hospitals:.4f},{factories:.4f},{offices:.4f},{house_a:.4f},{house_b:.4f}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_parse_forecast_accepts_ru_headers_without_tick_and_clips_to_horizon(app):
    with app.app_context():
        session = GameSession(title="RU weather", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.commit()

        forecast, diagnostics = parse_and_store_forecast(
            session_id=session.id,
            name="RU weather forecast",
            source_file="ru_weather.csv",
            content=_ru_weather_csv(99),
        )
        summary = summarize_forecast(forecast)

        assert diagnostics.errors == []
        assert diagnostics.warnings
        assert "tick" in diagnostics.warnings[0]
        assert summary["count"] == 48
        assert summary["tick_from"] == 0
        assert summary["tick_to"] == 47
        assert "wind_factor" in list(summary.get("factors_keys") or [])
        assert "solar_factor" in list(summary.get("factors_keys") or [])
        profiles = set(summary.get("profiles_keys") or [])
        assert {
            "hospital_load",
            "factory_load",
            "office_load",
            "house_a_load",
            "house_b_load",
        }.issubset(profiles)


def test_api_upload_accepts_ru_headers_without_tick(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="RU forecast upload")

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "RU upload",
            "file": (io.BytesIO(_ru_weather_csv(99)), "ru_weather.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    payload = upload.get_json()
    summary = payload["summary"]
    warnings = list((payload.get("diagnostics") or {}).get("warnings") or [])

    assert summary["count"] == 48
    assert summary["tick_from"] == 0
    assert summary["tick_to"] == 47
    assert summary["is_compatible"] is True
    assert warnings
    assert "tick" in warnings[0]
