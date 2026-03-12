from __future__ import annotations

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import EvaluationResult, GameSession

from tests.web_helpers import create_session, login


def _type_id_by_code(client, code: str) -> int:
    rows = client.get("/api/object-types").get_json()["items"]
    return int(next(row["id"] for row in rows if row["code"] == code))


def test_malformed_json_does_not_create_session(client, app):
    login(client, "admin", "admin123")

    with app.app_context():
        before = db.session.query(GameSession).count()

    resp = client.post("/api/sessions", data='{"bad"', content_type="application/json")
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"
    assert payload["error"]["message"] == "Некорректный JSON"

    with app.app_context():
        after = db.session.query(GameSession).count()
        assert after == before


def test_non_object_json_payload_is_rejected(client):
    login(client, "admin", "admin123")

    resp = client.post("/api/sessions", data="[]", content_type="application/json")
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"
    assert payload["error"]["message"] == "JSON payload должен быть объектом"


def test_malformed_json_does_not_persist_evaluation(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Malformed eval")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Eval lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    lot_id = int(lot_resp.get_json()["item"]["id"])

    with app.app_context():
        before = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).count()

    resp = client.post(
        f"/api/lots/{lot_id}/evaluate",
        data='{"bad"',
        content_type="application/json",
    )
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"

    with app.app_context():
        after = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).count()
        assert after == before


def test_list_lots_for_missing_session_returns_404(client):
    login(client, "admin", "admin123")

    resp = client.get("/api/lots?session_id=999999")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_admin_endpoints_return_404_for_missing_entities(client):
    login(client, "admin", "admin123")

    missing_ruleset = client.put("/api/rulesets/999999", json={"name": "x"})
    assert missing_ruleset.status_code == 404
    assert missing_ruleset.get_json()["error"]["code"] == "not_found"

    missing_pack = client.get("/api/start-pack-templates/999999")
    assert missing_pack.status_code == 404
    assert missing_pack.get_json()["error"]["code"] == "not_found"

    missing_type = client.put("/api/object-types/999999", json={"name": "x"})
    assert missing_type.status_code == 404
    assert missing_type.get_json()["error"]["code"] == "not_found"


def test_analyst_cannot_read_admin_api(client):
    login(client, "analyst", "analyst123")

    for path in (
        "/api/rulesets",
        "/api/start-pack-templates",
        "/api/start-pack-templates/1",
    ):
        resp = client.get(path)
        assert resp.status_code == 403
        payload = resp.get_json()
        assert payload["ok"] is False
        assert payload["error"]["code"] == "forbidden"


def test_object_crud_marks_session_evaluations_stale(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Object stale")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Object-sensitive lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    lot_id = int(lot_resp.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert rows
        assert all(not row.is_stale for row in rows)

    create_resp = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": wind_id,
            "custom_name": "Extra object",
        },
    )
    assert create_resp.status_code == 200
    object_id = int(create_resp.get_json()["item"]["id"])

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)
        for row in rows:
            row.is_stale = False
            row.stale_reason = ""
            db.session.add(row)
        db.session.commit()

    update_resp = client.put(
        f"/api/objects/{object_id}",
        json={
            "custom_name": "Extra object v2",
            "current_parameters": {"generation_mw": 11.0},
        },
    )
    assert update_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)
        for row in rows:
            row.is_stale = False
            row.stale_reason = ""
            db.session.add(row)
        db.session.commit()

    delete_resp = client.delete(f"/api/objects/{object_id}")
    assert delete_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)
