from __future__ import annotations

import io
import sqlite3

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import Forecast, GameSession, Ruleset, StartPackTemplate
from ies_bot_skeleton.web.services.seed import ensure_seed_data
from tests.web_helpers import ruleset_id_by_code


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


def _h48_csv_house_factory_market() -> bytes:
    rows = ["tick,wind,illumination,houseA,factory,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{3 + (tick % 5)},{0.5 + (tick % 4) * 0.1:.2f},{10 + (tick % 5)},{12 + (tick % 4)},{10 + (tick % 3)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_default_session_bootstraps_test_game_preset(client, app):
    _login(client, "admin", "admin123")

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "selected_strategy": "balanced",
        },
    )
    assert create_session_resp.status_code == 200
    session_id = int(create_session_resp.get_json()["item"]["id"])

    with app.app_context():
        session = db.session.get(GameSession, session_id)
        assert session is not None
        assert session.title == "Тестовая игра"
        assert session.selected_forecast_id is None
        ruleset = db.session.get(Ruleset, session.ruleset_id)
        assert ruleset is not None
        assert ruleset.code == "ies_test_game_2026"
        assert db.session.query(Forecast).filter_by(session_id=session_id).count() == 0

    objects_resp = client.get(f"/api/objects?session_id={session_id}")
    assert objects_resp.status_code == 200
    assert len(objects_resp.get_json()["items"]) == 4

    lots_resp = client.get(f"/api/lots?session_id={session_id}")
    assert lots_resp.status_code == 200
    assert len(lots_resp.get_json()["items"]) == 5

    add_start_pack_resp = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert add_start_pack_resp.status_code == 400
    assert add_start_pack_resp.get_json()["ok"] is False


def test_failed_test_game_bootstrap_rolls_back_session_creation(client, app):
    _login(client, "admin", "admin123")

    with app.app_context():
        before = db.session.query(GameSession).count()
        for template in db.session.query(StartPackTemplate).all():
            template.is_active = False
            db.session.add(template)
        db.session.commit()

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "selected_strategy": "balanced",
        },
    )
    assert create_session_resp.status_code == 400
    assert create_session_resp.get_json()["ok"] is False

    with app.app_context():
        assert db.session.query(GameSession).count() == before


def test_create_session_rejects_selected_forecast_id(client, app):
    _login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")

    with app.app_context():
        foreign_session = GameSession(
            title="Foreign forecast source",
            ruleset_id=ruleset_id,
            selected_strategy="balanced",
        )
        db.session.add(foreign_session)
        db.session.flush()
        foreign_forecast = Forecast(
            session_id=foreign_session.id,
            name="Foreign forecast",
            source_file="foreign.csv",
            column_map_json={},
            metadata_json={},
        )
        db.session.add(foreign_forecast)
        db.session.commit()
        foreign_forecast_id = int(foreign_forecast.id)
        before = db.session.query(GameSession).count()

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "title": "Should reject foreign forecast",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "selected_forecast_id": foreign_forecast_id,
        },
    )
    assert create_session_resp.status_code == 400
    payload = create_session_resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"
    assert "selected_forecast_id" in payload["error"]["message"]

    with app.app_context():
        assert db.session.query(GameSession).count() == before


def test_explicit_generic_ruleset_session_stays_empty(client):
    _login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "title": "Explicit generic",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
        },
    )
    assert create_session_resp.status_code == 200
    session_id = int(create_session_resp.get_json()["item"]["id"])

    objects_resp = client.get(f"/api/objects?session_id={session_id}")
    assert objects_resp.status_code == 200
    assert objects_resp.get_json()["items"] == []

    lots_resp = client.get(f"/api/lots?session_id={session_id}")
    assert lots_resp.status_code == 200
    assert lots_resp.get_json()["items"] == []


def test_forecast_upload_uses_test_game_default_name(client):
    _login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    create_session_resp = client.post(
        "/api/sessions",
        json={
            "title": "Forecast naming",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
        },
    )
    assert create_session_resp.status_code == 200
    session_id = int(create_session_resp.get_json()["item"]["id"])

    upload_resp = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "file": (io.BytesIO(_h48_csv_house_factory_market()), "forecast.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload_resp.status_code == 200
    assert upload_resp.get_json()["item"]["name"] == "Прогноз игры"


def test_admin_end_to_end_flow(client, app):
    _login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")

    create_session_resp = client.post(
        "/api/sessions",
        json={
            "title": "Integration Session",
            "ruleset_id": ruleset_id,
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

    csv_payload = io.BytesIO(_h48_csv_house_factory_market())
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
    assert "budget_adjusted_bid" in eval_json["item"]
    assert "system_check" in eval_json["item"]
    assert eval_json["item"]["forecast_context"]["source"] == "selected_forecast"

    analytics_resp = client.get(
        f"/api/sessions/{session_id}/lots/analytics?status=available&sort=utility_desc"
    )
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
    ruleset_id = ruleset_id_by_code(client, "ies_2026")

    create_resp = client.post(
        "/api/sessions",
        json={
            "title": "Roundtrip",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "budget_total": 1000,
        },
    )
    session_id = int(create_resp.get_json()["item"]["id"])

    export_payload = client.get(f"/api/sessions/{session_id}/export.json").get_json()["item"]
    imported = client.post("/api/sessions/import", json=export_payload)
    assert imported.status_code == 200
    assert imported.get_json()["ok"] is True


def test_import_session_backward_compatible_with_schema_v4(client):
    _login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")

    create_resp = client.post(
        "/api/sessions",
        json={
            "title": "Roundtrip v4",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "budget_total": 1000,
        },
    )
    session_id = int(create_resp.get_json()["item"]["id"])
    export_payload = client.get(f"/api/sessions/{session_id}/export.json").get_json()["item"]
    export_payload["schema_version"] = 4

    imported = client.post("/api/sessions/import", json=export_payload)
    assert imported.status_code == 200
    assert imported.get_json()["ok"] is True


def test_create_app_bootstraps_local_sqlite_when_only_alembic_version_exists(tmp_path):
    db_path = tmp_path / "bootstrap.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version (version_num) VALUES ('head')")
    conn.commit()
    conn.close()

    app = create_app("testing")
    app.config.update(TESTING=False, SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}")

    from ies_bot_skeleton.web.app import _bootstrap_local_sqlite

    _bootstrap_local_sqlite(app)

    with app.app_context():
        tables = {
            row[0]
            for row in db.session.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        }
        assert "users" in tables
        assert "rulesets" in tables
        assert "object_types" in tables

    client = app.test_client()
    resp = client.get("/login")
    assert resp.status_code == 200
