from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _build_two_lots(client, session_id: int):
    tmap = _type_map(client)
    assert (
        client.post(
            "/api/lots",
            json={
                "session_id": session_id,
                "name": "Lot A",
                "scope": "normal",
                "base_bid": 100,
                "current_bid": 100,
                "items": [
                    {"object_type_id": tmap["wind"], "quantity": 1},
                    {"object_type_id": tmap["storage"], "quantity": 1},
                ],
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/lots",
            json={
                "session_id": session_id,
                "name": "Lot B",
                "scope": "normal",
                "base_bid": 95,
                "current_bid": 95,
                "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
            },
        ).status_code
        == 200
    )


def test_recommend_and_quick_pages_render_forecast_only_blocks(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Quick forecast")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    _build_two_lots(client, session_id)

    compare_resp = client.get(f"/compare/{session_id}")
    assert compare_resp.status_code == 404

    recommend_resp = client.get(f"/recommend/{session_id}", follow_redirects=False)
    assert recommend_resp.status_code in (302, 303)
    assert recommend_resp.headers["Location"].endswith(f"/sessions/{session_id}")

    quick_resp = client.get(f"/quick-auction/{session_id}")
    assert quick_resp.status_code == 200
    quick_html = quick_resp.get_data(as_text=True)
    assert "Быстрый аукцион" in quick_html
    assert "Рабочая ставка" in quick_html
    assert "Ставка с учетом бюджета" in quick_html
    assert "Открыть сравнение" not in quick_html
    assert "Технический ответ" not in quick_html
    assert "Купить" in quick_html
