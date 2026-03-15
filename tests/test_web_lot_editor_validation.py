from __future__ import annotations

import json

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _wind_id(client) -> int:
    return _type_map(client)["wind"]


def test_lot_editor_prefers_visual_payload(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lot editor")
    wind_id = _wind_id(client)

    form_payload = {
        "session_id": str(session_id),
        "name": "Visual lot",
        "scope": "normal",
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


def test_lot_editor_renders_single_hidden_state_fields(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lot editor html")

    resp = client.get(f"/lots/{session_id}/edit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count('name="items_state_json"') == 1
    assert html.count('name="items_json"') == 1
    assert html.count('name="session_id"') == 1


def test_lot_editor_rejects_invalid_lot_payload(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Invalid lot")
    wind_id = _wind_id(client)

    form_payload = {
        "session_id": str(session_id),
        "name": "Bad lot",
        "scope": "normal",
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


def test_lot_delete_confirm_page_and_post_remove_lot(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Delete lot")
    type_map = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot to delete",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": type_map["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])

    confirm = client.get(f"/lots/item/{lot_id}/delete")
    assert confirm.status_code == 200
    html = confirm.get_data(as_text=True)
    assert "Удаление лота" in html
    assert "Lot to delete" in html

    delete_resp = client.post(f"/lots/item/{lot_id}/delete", data={}, follow_redirects=False)
    assert delete_resp.status_code in (302, 303)
    assert delete_resp.headers["Location"].endswith(f"/lots/{session_id}")

    lots_resp = client.get(f"/api/lots?session_id={session_id}")
    assert lots_resp.status_code == 200
    assert lots_resp.get_json()["items"] == []


def test_lot_detail_and_edit_flow(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lot detail")
    type_map = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot detail source",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": type_map["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])

    detail_resp = client.get(f"/lots/item/{lot_id}")
    assert detail_resp.status_code == 200
    detail_html = detail_resp.get_data(as_text=True)
    assert "Lot detail source" in detail_html
    assert "Декомпозиция расчёта" in detail_html
    assert "Worst" in detail_html
    assert "Base" in detail_html
    assert "Best" in detail_html

    strategy_fit_redirect = client.get(f"/strategy-fit/{lot_id}", follow_redirects=False)
    assert strategy_fit_redirect.status_code in (302, 303)
    assert strategy_fit_redirect.headers["Location"].endswith(f"/lots/item/{lot_id}")

    edit_form = {
        "session_id": str(session_id),
        "name": "Lot updated",
        "scope": "global",
        "base_bid": "140",
        "current_bid": "145",
        "available_round": "2",
        "note": "Updated note",
        "items_state_json": json.dumps(
            [
                {"object_type_id": type_map["wind"], "quantity": 2},
                {"object_type_id": type_map["storage"], "quantity": 1},
            ]
        ),
        "items_json": "[]",
    }
    update_resp = client.post(f"/lots/item/{lot_id}/edit", data=edit_form, follow_redirects=False)
    assert update_resp.status_code in (302, 303)
    assert update_resp.headers["Location"].endswith(f"/lots/item/{lot_id}")

    api_lot = client.get(f"/api/lots/{lot_id}")
    assert api_lot.status_code == 200
    payload = api_lot.get_json()["item"]
    assert payload["name"] == "Lot updated"
    assert payload["scope"] == "global"
    assert len(payload["items"]) == 2


def test_forecast_only_evaluation_uses_bundled_fallback_and_returns_explanation(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Meaningful eval")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200

    type_map = _type_map(client)
    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Profitable lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [
                {"object_type_id": type_map["wind"], "quantity": 1},
                {"object_type_id": type_map["storage"], "quantity": 1},
            ],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    payload = eval_resp.get_json()
    assert payload["ok"] is True

    item = payload["item"]
    assert item["summary_score"] != 0
    assert item["forecast_context"]["source"] == "bundled_forecast"
    assert item["recommended_bid_hard"] >= 0
    assert item["budget_adjusted_bid"] <= item["portfolio_context"]["remaining_budget"] + 1e-9
    assert item["metrics"]["bids"]["valuation_model"]["model"] == "valuation_model_v3"
    assert item["metrics"]["delta_score"] != 0
    assert item["risk_commentary"]
    assert item["explanation"]
    assert "Недостаточно данных" not in item["explanation"]
    assert "не рекомендует ставку" not in item["strategy_fit_text"]
