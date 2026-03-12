from __future__ import annotations

import io

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.services.seed import ensure_seed_data


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    resp = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "remember": "y",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def test_admin_end_to_end_flow(client, app):
    _login(client, "admin", "admin123")

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "title": "Integration Session",
            "selected_strategy": "balanced",
            "budget_total": 5000,
        },
    )
    assert create_session_resp.status_code == 200
    session_payload = create_session_resp.get_json()
    assert session_payload["ok"] is True
    session_id = int(session_payload["item"]["id"])

    start_pack_resp = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert start_pack_resp.status_code == 200
    assert start_pack_resp.get_json()["ok"] is True
    assert "template_id" in start_pack_resp.get_json()

    object_types_resp = client.get("/api/object-types")
    assert object_types_resp.status_code == 200
    types = object_types_resp.get_json()["items"]
    assert any(x["code"] == "wind" for x in types)

    rulesets_resp = client.get("/api/rulesets")
    assert rulesets_resp.status_code == 200
    assert any("model_settings" in row for row in rulesets_resp.get_json()["items"])

    wind_id = next(x["id"] for x in types if x["code"] == "wind")
    storage_id = next(x["id"] for x in types if x["code"] == "storage")

    lot_a_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot A",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [
                {"object_type_id": wind_id, "quantity": 1},
                {"object_type_id": storage_id, "quantity": 1},
            ],
        },
    )
    assert lot_a_resp.status_code == 200
    lot_a_id = int(lot_a_resp.get_json()["item"]["id"])

    lot_b_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot B",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_b_resp.status_code == 200
    assert int(lot_b_resp.get_json()["item"]["id"]) > 0

    csv_payload = io.BytesIO(
        b"tick,wind,illumination,houseA,factory,market_price\n0,3,0.7,10,12,11\n1,4,0.8,11,13,10\n"
    )
    upload_resp = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Forecast 1",
            "file": (csv_payload, "forecast.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload_resp.status_code == 200
    forecast_json = upload_resp.get_json()
    assert forecast_json["ok"] is True
    assert int(forecast_json["item"]["id"]) > 0

    eval_resp = client.post(
        f"/api/lots/{lot_a_id}/evaluate",
        json={},
    )
    assert eval_resp.status_code == 200
    eval_json = eval_resp.get_json()
    assert eval_json["ok"] is True
    assert "summary_score" in eval_json["item"]
    assert "recommended_bid_hard" in eval_json["item"]
    assert eval_json["item"]["forecast_context"]["source"] == "selected_forecast"

    analytics_resp = client.get(f"/api/sessions/{session_id}/lots/analytics?status=available&sort=utility_desc")
    assert analytics_resp.status_code == 200
    analytics_json = analytics_resp.get_json()
    assert analytics_json["ok"] is True
    assert len(analytics_json["items"]) == 2
    assert analytics_json["items"][0]["forecast_context"]["source"] == "selected_forecast"

    rec_resp = client.post(
        "/api/recommend/best-lot",
        json={"session_id": session_id},
    )
    assert rec_resp.status_code == 200
    rec_json = rec_resp.get_json()
    assert rec_json["ok"] is True
    assert rec_json["item"]["best"] is not None
    assert rec_json["item"]["best"]["forecast_context"]["source"] == "selected_forecast"

    export_json_resp = client.get(f"/api/sessions/{session_id}/export.json")
    assert export_json_resp.status_code == 200
    assert export_json_resp.get_json()["ok"] is True
    assert "schema_version" in export_json_resp.get_json()["item"]

    export_csv_resp = client.get(f"/api/sessions/{session_id}/evaluations.csv")
    assert export_csv_resp.status_code == 200
    assert b"evaluation_id" in export_csv_resp.data


def test_analyst_cannot_modify_object_types(client):
    _login(client, "analyst", "analyst123")
    resp = client.post(
        "/api/object-types",
        json={
            "code": "test_object",
            "name": "Test",
            "category": "consumer",
        },
    )
    assert resp.status_code == 403


def test_import_session_roundtrip(client):
    _login(client, "admin", "admin123")

    create_resp = client.post(
        "/api/sessions",
        json={"title": "Roundtrip", "selected_strategy": "balanced", "budget_total": 1000},
    )
    session_id = int(create_resp.get_json()["item"]["id"])

    export_payload = client.get(f"/api/sessions/{session_id}/export.json").get_json()["item"]
    imported = client.post("/api/sessions/import", json=export_payload)
    assert imported.status_code == 200
    assert imported.get_json()["ok"] is True
