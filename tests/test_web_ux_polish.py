from __future__ import annotations

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
