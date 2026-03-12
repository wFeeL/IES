from __future__ import annotations

import io

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _create_lot(client, session_id: int) -> int:
    tmap = _type_map(client)
    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Context lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [
                {"object_type_id": tmap["wind"], "quantity": 1},
                {"object_type_id": tmap["storage"], "quantity": 1},
            ],
        },
    )
    assert created.status_code == 200
    return int(created.get_json()["item"]["id"])


def test_evaluation_uses_session_mode_and_bundled_forecast_fallback(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Bundled fallback")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    lot_id = _create_lot(client, session_id)

    settings_resp = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"analysis_mode": "forecast", "selected_forecast_id": None},
    )
    assert settings_resp.status_code == 200

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    item = eval_resp.get_json()["item"]

    assert item["analysis_context"]["mode"] == "forecast"
    assert item["analysis_context"]["source"] == "bundled_forecast"
    assert item["analysis_context"]["forecast_id"] is None
    assert item["forecast_summary"]["name"] == "Встроенный базовый прогноз"


def test_evaluation_uses_selected_forecast_from_session_when_mode_not_passed(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Selected forecast")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    lot_id = _create_lot(client, session_id)

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Uploaded forecast",
            "file": (io.BytesIO(b"tick,wind,illumination,houseA,market_price\n0,3,0.6,10,12\n"), "f.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    forecast_id = int(upload.get_json()["item"]["id"])

    settings_resp = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"analysis_mode": "forecast", "selected_forecast_id": forecast_id},
    )
    assert settings_resp.status_code == 200

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    item = eval_resp.get_json()["item"]

    assert item["analysis_context"]["mode"] == "forecast"
    assert item["analysis_context"]["source"] == "selected_forecast"
    assert item["analysis_context"]["forecast_id"] == forecast_id
    assert item["forecast_summary"]["forecast_id"] == forecast_id


def test_api_errors_use_structured_schema(client):
    login(client, "admin", "admin123")

    resp = client.get("/api/lots/999999")

    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
    assert "не найден" in payload["error"]["message"]
