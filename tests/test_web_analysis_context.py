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


def _h48_csv_house_market() -> bytes:
    rows = ["tick,wind,illumination,houseA,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{3 + (tick % 5)},{0.4 + (tick % 6) * 0.1:.2f},{10 + (tick % 4)},{11 + (tick % 3)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_evaluation_uses_bundled_forecast_when_session_has_no_selected_forecast(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Bundled fallback")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    lot_id = _create_lot(client, session_id)

    settings_resp = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"selected_forecast_id": None},
    )
    assert settings_resp.status_code == 200

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    item = eval_resp.get_json()["item"]

    assert item["forecast_context"]["source"] == "bundled_forecast"
    assert item["forecast_context"]["forecast_id"] is None
    assert item["analysis_context"]["mode"] == "forecast"
    assert item["forecast_summary"]["name"] == "Прогноз тестовой игры"


def test_evaluation_uses_selected_forecast_from_session(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Selected forecast")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    lot_id = _create_lot(client, session_id)

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Uploaded forecast",
            "file": (io.BytesIO(_h48_csv_house_market()), "f.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    forecast_id = int(upload.get_json()["item"]["id"])

    settings_resp = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"selected_forecast_id": forecast_id},
    )
    assert settings_resp.status_code == 200

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    item = eval_resp.get_json()["item"]

    assert item["forecast_context"]["source"] == "selected_forecast"
    assert item["forecast_context"]["forecast_id"] == forecast_id
    assert item["forecast_summary"]["source_kind"] == "selected_forecast"


def test_api_errors_use_structured_schema(client):
    login(client, "admin", "admin123")

    resp = client.get("/api/lots/999999")

    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
    assert "не найден" in payload["error"]["message"]
