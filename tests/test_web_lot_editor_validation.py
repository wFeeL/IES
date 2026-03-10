from __future__ import annotations

import json

from tests.web_helpers import create_session, login


def _wind_id(client) -> int:
    rows = client.get("/api/object-types").get_json()["items"]
    return int(next(row["id"] for row in rows if row["code"] == "wind"))


def test_lot_editor_prefers_visual_payload(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lot editor")
    wind_id = _wind_id(client)

    form_payload = {
        "session_id": str(session_id),
        "name": "Visual lot",
        "scope": "normal",
        "status": "available",
        "base_bid": "100",
        "current_bid": "100",
        "available_round": "1",
        "note": "",
        "items_state_json": json.dumps([{"object_type_id": wind_id, "quantity": 1}]),
        "items_json": "[]",
    }
    resp = client.post(f"/lots/{session_id}/edit", data=form_payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    lots_resp = client.get(f"/api/lots?session_id={session_id}")
    assert lots_resp.status_code == 200
    lots = lots_resp.get_json()["items"]
    assert lots
    assert lots[0]["items"][0]["object_type_id"] == wind_id



def test_lot_editor_rejects_invalid_lot_payload(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Invalid lot")
    wind_id = _wind_id(client)

    form_payload = {
        "session_id": str(session_id),
        "name": "Bad lot",
        "scope": "normal",
        "status": "available",
        "base_bid": "100",
        "current_bid": "100",
        "available_round": "1",
        "note": "",
        "items_state_json": json.dumps([{"object_type_id": wind_id, "quantity": 0}]),
        "items_json": "[]",
    }
    resp = client.post(f"/lots/{session_id}/edit", data=form_payload, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "quantity" in html



def test_api_lot_validation_guards_invalid_items(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="API validation")

    bad_type = client.post(
        "/api/lots",
        json={"session_id": session_id, "name": "Bad", "items": "not-list"},
    )
    assert bad_type.status_code == 400

    bad_empty = client.post(
        "/api/lots",
        json={"session_id": session_id, "name": "Bad", "items": []},
    )
    assert bad_empty.status_code == 400
