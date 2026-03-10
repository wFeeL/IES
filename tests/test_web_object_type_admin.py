from __future__ import annotations

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import EvaluationResult

from tests.web_helpers import create_session, login


def _type_id_by_code(client, code: str) -> int:
    resp = client.get("/api/object-types")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    return int(next(row["id"] for row in items if row["code"] == code))


def test_object_type_update_marks_related_evaluations_stale(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Stale by type")

    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200

    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Wind lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_resp.status_code == 200
    lot_id = int(lot_resp.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={"mode": "forecast"})
    assert eval_resp.status_code == 200

    with app.app_context():
        before = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert before
        assert all(not row.is_stale for row in before)

    update_resp = client.put(
        f"/api/object-types/{wind_id}",
        json={"default_parameters": {"generation_mw": 99.0}},
    )
    assert update_resp.status_code == 200

    with app.app_context():
        after = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert after
        assert any(row.is_stale for row in after)



def test_object_type_soft_delete_and_include_inactive_scope(client):
    login(client, "admin", "admin123")
    wind_id = _type_id_by_code(client, "wind")

    delete_resp = client.delete(f"/api/object-types/{wind_id}")
    assert delete_resp.status_code == 200

    admin_list = client.get("/api/object-types?include_inactive=1")
    assert admin_list.status_code == 200
    admin_rows = admin_list.get_json()["items"]
    wind_row = next(row for row in admin_rows if int(row["id"]) == wind_id)
    assert wind_row["is_active"] is False

    client.post("/logout", data={})
    login(client, "analyst", "analyst123")

    analyst_list = client.get("/api/object-types?include_inactive=1")
    assert analyst_list.status_code == 200
    analyst_rows = analyst_list.get_json()["items"]
    assert all(row["is_active"] for row in analyst_rows)

    forbidden = client.put(f"/api/object-types/{wind_id}", json={"name": "Forbidden"})
    assert forbidden.status_code == 403
