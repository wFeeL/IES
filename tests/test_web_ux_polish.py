from __future__ import annotations

import json

from tests.web_helpers import create_session, login


def test_dashboard_uses_session_terms_and_strategy_help(client):
    login(client, "admin", "admin123")

    session_id = create_session(client, title="UX session", selected_strategy="eco")

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Создать сессию" in html
    assert "Пояснение к стратегии" in html
    assert "Открыть сессию" in html
    assert "Открыть рабочее место" not in html
    assert "Подготовить рабочее место" not in html
    assert "Экологическая" in html
    assert f"/sessions/{session_id}" in html


def test_session_can_be_deleted_via_ssr_confirm_page(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Delete me")

    confirm = client.get(f"/sessions/{session_id}/delete")
    assert confirm.status_code == 200
    confirm_html = confirm.get_data(as_text=True)
    assert "Удаление сессии" in confirm_html
    assert "Delete me" in confirm_html

    resp = client.post(f"/sessions/{session_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Сессия «Delete me» удалена" in html
    assert f"/sessions/{session_id}" not in html
    assert "Создать сессию" in html


def test_catalog_renders_glossary_for_russian_users(client):
    login(client, "admin", "admin123")

    resp = client.get("/catalog")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Англо-русский словарь терминов" in html
    assert "generation_mw" in html
    assert "объём генерации" in html
    assert "consumer" in html
    assert "потребители" in html


def test_admin_edit_forms_hide_raw_json_and_show_typed_sections(client):
    login(client, "admin", "admin123")

    object_type_resp = client.get("/admin/object-types/new")
    assert object_type_resp.status_code == 200
    object_type_html = object_type_resp.get_data(as_text=True)
    assert "JSON параметров по умолчанию" not in object_type_html
    assert "JSON редактируемых полей" not in object_type_html
    assert "JSON правил" not in object_type_html
    assert "Разрешить редактирование этого параметра" in object_type_html
    assert "Ограничения и логика" in object_type_html

    ruleset_resp = client.get("/admin/rulesets/new")
    assert ruleset_resp.status_code == 200
    ruleset_html = ruleset_resp.get_data(as_text=True)
    assert "JSON конфигурации" not in ruleset_html
    assert "JSON настроек модели" not in ruleset_html
    assert "Профили стратегий" in ruleset_html
    assert "Сценарии" in ruleset_html
    assert "Рынок" in ruleset_html


def test_system_and_lot_edit_pages_hide_unwanted_analysis_noise(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Noise check")

    system_resp = client.get(f"/system/{session_id}")
    assert system_resp.status_code == 200
    system_html = system_resp.get_data(as_text=True)
    assert "Режим анализа" not in system_html

    lot_edit_resp = client.get(f"/lots/{session_id}/edit")
    assert lot_edit_resp.status_code == 200
    lot_edit_html = lot_edit_resp.get_data(as_text=True)
    assert "raw JSON" not in lot_edit_html
    assert "Состав JSON" not in lot_edit_html


def test_workbench_exposes_history_and_export_actions(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Workbench actions")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert f"/evaluation/{session_id}" in html
    assert f"/api/sessions/{session_id}/export.json" in html
    assert f"/api/sessions/{session_id}/evaluations.csv" in html


def test_dashboard_imports_session_via_ssr_form(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Export me")
    payload = client.get(f"/api/sessions/{session_id}/export.json").get_json()["item"]

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "/sessions/import" in dashboard.get_data(as_text=True)

    imported = client.post(
        "/sessions/import",
        data={"payload_json": json.dumps(payload)},
        follow_redirects=False,
    )
    assert imported.status_code in (302, 303)
    assert "/sessions/" in imported.headers["Location"]


def test_quick_auction_uses_user_facing_actions_without_debug_block(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Quick UX")
    type_rows = client.get("/api/object-types").get_json()["items"]
    wind_id = next(row["id"] for row in type_rows if row["code"] == "wind")

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "QA lot 1",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 105,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    ).get_json()["item"]["id"]
    client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "QA lot 2",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 95,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )

    resp = client.get(f"/quick-auction/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Технический ответ" not in html
    assert "Текущая ставка по лоту" in html
    assert f"/lots/item/{lot_a}" in html
