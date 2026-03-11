from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _build_two_lots(client, session_id: int):
    tmap = _type_map(client)
    lot_a = client.post(
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
    )
    assert lot_a.status_code == 200

    lot_b = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot B",
            "scope": "normal",
            "base_bid": 95,
            "current_bid": 95,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot_b.status_code == 200



def test_compare_recommend_and_quick_pages_render_extended_blocks(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Compare quick")
    assert client.post(f"/api/sessions/{session_id}/add-start-pack", json={}).status_code == 200
    _build_two_lots(client, session_id)

    compare_resp = client.get(f"/compare/{session_id}")
    assert compare_resp.status_code == 200
    compare_html = compare_resp.get_data(as_text=True)
    assert "Соответствие стратегии" in compare_html
    assert "Пояснение" in compare_html

    recommend_resp = client.get(f"/recommend/{session_id}")
    assert recommend_resp.status_code == 200
    recommend_html = recommend_resp.get_data(as_text=True)
    assert "Рекомендация по лучшему лоту" in recommend_html
    assert "risk" in recommend_html.lower() or "Риск" in recommend_html

    quick_resp = client.get(f"/quick-auction/{session_id}")
    assert quick_resp.status_code == 200
    quick_html = quick_resp.get_data(as_text=True)
    assert "Рекоменд. ставка" in quick_html
    assert "Соотв. стратегии" in quick_html
    assert "qaApplyFilters" in quick_html
    assert "quickEvalRow" in quick_html
