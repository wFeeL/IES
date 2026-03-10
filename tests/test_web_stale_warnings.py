from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def test_stale_warning_banner_and_warn_only_behavior(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Stale warnings")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200

    tmap = _type_map(client)

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot A",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot_a.status_code == 200
    lot_a_id = int(lot_a.get_json()["item"]["id"])

    lot_b = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot B",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": tmap["storage"], "quantity": 1}],
        },
    )
    assert lot_b.status_code == 200
    lot_b_id = int(lot_b.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_a_id}/evaluate", json={"mode": "forecast"})
    assert eval_resp.status_code == 200

    upd = client.put(
        f"/api/object-types/{tmap['wind']}",
        json={"default_parameters": {"generation_mw": 77.0}},
    )
    assert upd.status_code == 200

    session_page = client.get(f"/sessions/{session_id}")
    assert session_page.status_code == 200
    html = session_page.get_data(as_text=True)
    assert "Обнаружены устаревшие оценки" in html

    # warn-only: compare still available
    compare_resp = client.post(
        "/api/lots/compare",
        json={"session_id": session_id, "lot_ids": [lot_a_id, lot_b_id], "mode": "forecast"},
    )
    assert compare_resp.status_code == 200
    assert compare_resp.get_json()["ok"] is True
