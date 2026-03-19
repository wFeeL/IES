from __future__ import annotations

import pytest

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
    assert summary["start_budget"] == pytest.approx(summary["budget_total"])
    assert summary["remaining_budget"] < summary["budget_total"]

    lot_payload = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert lot_payload["status"] == "bought"
    assert lot_payload["purchase_price"] == 130.0

    objects = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    assert any(int(obj.get("source_lot_id") or 0) == lot_id for obj in objects)

    undo_resp = client.post(f"/api/lots/{lot_id}/undo-buy", json={})
    assert undo_resp.status_code == 200
    undo_summary = undo_resp.get_json()["item"]
    assert undo_summary["start_budget"] == pytest.approx(undo_summary["budget_total"])
    assert undo_summary["remaining_budget"] == undo_summary["budget_total"]

    lot_payload = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert lot_payload["status"] == "available"
    assert lot_payload["purchase_price"] is None


def test_session_budget_summary_includes_allpay_spend(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Allpay budget")

    update = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"allpay_spent": 37.5},
    )
    assert update.status_code == 200

    session_payload = client.get(f"/api/sessions/{session_id}").get_json()["item"]
    assert session_payload["start_budget"] == pytest.approx(session_payload["budget_total"])
    assert session_payload["allpay_spent"] == 37.5
    assert session_payload["remaining_budget"] == session_payload["budget_total"] - 37.5
    settings_payload = client.get(f"/api/sessions/{session_id}/analysis-settings").get_json()["item"]
    assert settings_payload["start_budget"] == pytest.approx(session_payload["start_budget"])

    dashboard = client.get(f"/sessions/{session_id}")
    assert dashboard.status_code == 200
    html = dashboard.get_data(as_text=True)
    assert "37.5" in html or "37,5" in html


def test_bought_generated_objects_have_pending_and_integrated_lifecycle(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Portfolio integration lifecycle")
    type_map = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lifecycle lot",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": type_map["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])

    buy_resp = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 65.0})
    assert buy_resp.status_code == 200

    objects = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    generated = [row for row in objects if int(row.get("source_lot_id") or 0) == lot_id]
    assert generated
    assert all(str(row.get("integration_state")) == "pending_connection" for row in generated)
    assert all(bool(row.get("requires_integration")) for row in generated)

    warning_page = client.get(f"/sessions/{session_id}")
    assert warning_page.status_code == 200
    assert "нужно подключить" in warning_page.get_data(as_text=True).lower()

    parent_codes = {"main_substation", "main", "main_substation_hq", "mini_substation_a", "mini_substation_b", "mini"}
    parent_candidates = [
        row
        for row in objects
        if int(row.get("source_lot_id") or 0) != lot_id
        and str(row.get("object_type_code") or "") in parent_codes
    ]
    if not parent_candidates:
        main_created = client.post(
            "/api/objects",
            json={
                "session_id": session_id,
                "object_type_id": type_map["main_substation"],
                "custom_name": "Main for integration",
                "parent_instance_id": None,
            },
        )
        assert main_created.status_code == 200
        parent_candidates = [main_created.get_json()["item"]]
    parent_id = int(parent_candidates[0]["id"])
    target = generated[0]
    update = client.put(
        f"/api/objects/{int(target['id'])}",
        json={"parent_instance_id": parent_id},
    )
    assert update.status_code == 200
    updated = update.get_json()["item"]
    assert str(updated.get("integration_state")) == "integrated"
    assert bool(updated.get("requires_integration")) is False

    refreshed = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    assert all(
        str(row.get("integration_state")) == "integrated"
        for row in refreshed
        if int(row.get("source_lot_id") or 0) == lot_id
    )

    clean_page = client.get(f"/sessions/{session_id}")
    assert clean_page.status_code == 200
    assert "нужно подключить" not in clean_page.get_data(as_text=True).lower()
