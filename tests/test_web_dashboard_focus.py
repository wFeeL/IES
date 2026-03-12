from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def test_lots_table_uses_compact_headers_and_actions_menu(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lots table")
    type_map = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Очень длинное имя лота для проверки tooltip и ellipsis",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 120,
            "items": [
                {"object_type_id": type_map["wind"], "quantity": 1},
                {"object_type_id": type_map["storage"], "quantity": 1},
            ],
        },
    )
    assert created.status_code == 200

    resp = client.get(f"/lots/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Полезность" in html
    assert "Чистая прибыль" in html
    assert "Макс. ставка" in html
    assert "Ещё" in html
    assert 'aria-haspopup="menu"' in html
    assert 'role="menu"' in html
    assert 'title="Очень длинное имя лота для проверки tooltip и ellipsis"' in html


def test_session_dashboard_shows_forecast_portfolio_and_quick_actions(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Dashboard focus")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Сессия → Прогноз → Лоты → Покупка" in html
    assert "Активный прогноз" in html
    assert "Готовность данных" in html
    assert "Бюджет и портфель" in html
    assert "Лоты" in html
    assert "Прогноз" in html
    assert "Быстрый аукцион" in html
    assert "Проверить энергосистему" in html
    assert "Экспорт сессии" in html


def test_theme_toggle_and_css_tokens_present(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Theme smoke")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="themeToggle"' in html

    css_resp = client.get("/static/css/tailwind.css")
    assert css_resp.status_code == 200
    css = css_resp.get_data(as_text=True)
    assert "html[data-theme=\"dark\"]" in css
    assert "overflow-wrap: anywhere" not in css
    assert "overflow-wrap: break-word" not in css
    assert ".row-actions-menu" in css
