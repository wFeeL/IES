from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def test_buy_and_undo_lot_updates_budget_and_materializes_objects(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Portfolio buy")
    type_map = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Portfolio lot",
            "scope": "normal",
            "base_bid": 120,
            "current_bid": 125,
            "items": [
                {"object_type_id": type_map["wind"], "quantity": 2},
                {"object_type_id": type_map["storage"], "quantity": 1},
            ],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])

    buy_page = client.get(f"/lots/item/{lot_id}/buy")
    assert buy_page.status_code == 200
    assert "Фактическая цена покупки" in buy_page.get_data(as_text=True)

    buy_resp = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 130.0})
    assert buy_resp.status_code == 200
    summary = buy_resp.get_json()["item"]
    assert summary["purchase_price"] == 130.0
    assert summary["remaining_budget"] < summary["budget_total"]

    lot_payload = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert lot_payload["status"] == "bought"
    assert lot_payload["purchase_price"] == 130.0

    objects = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    assert any(int(obj.get("source_lot_id") or 0) == lot_id for obj in objects)

    undo_resp = client.post(f"/api/lots/{lot_id}/undo-buy", json={})
    assert undo_resp.status_code == 200
    undo_summary = undo_resp.get_json()["item"]
    assert undo_summary["remaining_budget"] == undo_summary["budget_total"]

    lot_payload = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert lot_payload["status"] == "available"
    assert lot_payload["purchase_price"] is None
