from __future__ import annotations

from tests.web_helpers import create_session, login


def test_second_level_pages_have_back_breadcrumbs_and_cancel(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Nav")

    resp = client.get(f"/lots/{session_id}/edit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert f'href="/lots/{session_id}"' in html  # back fallback and cancel link target
    assert "Назад" in html
    assert "Новый лот" in html
    assert "Отмена" in html


def test_back_url_ignores_external_referrer(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="External Ref")

    resp = client.get(
        f"/lots/{session_id}/edit",
        headers={"Referer": "https://evil.example/path"},
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "https://evil.example/path" not in html
    assert f'href="/lots/{session_id}"' in html


def test_login_next_guard_blocks_open_redirect(client):
    resp = client.post(
        "/login?next=https://evil.example/phish",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "/dashboard" in location
    assert "evil.example" not in location


def test_login_next_allows_internal_redirect(client):
    resp = client.post(
        "/login?next=/catalog",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/catalog")


def test_missing_session_redirects_with_flash_message(client):
    login(client, "admin", "admin123")

    resp = client.get("/sessions/999999", follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Сессия не найдена." in html


def test_missing_lot_redirects_with_flash_message(client):
    login(client, "admin", "admin123")

    resp = client.get("/lots/item/999999", follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Лот 999999 не найден." in html


def test_missing_admin_entities_redirect_with_flash_message(client):
    login(client, "admin", "admin123")

    ruleset_resp = client.get("/admin/rulesets/999999/edit", follow_redirects=True)
    assert ruleset_resp.status_code == 200
    assert "Набор правил не найден." in ruleset_resp.get_data(as_text=True)

    pack_resp = client.get("/admin/start-packs/999999/edit", follow_redirects=True)
    assert pack_resp.status_code == 200
    assert "Шаблон стартового пакета не найден." in pack_resp.get_data(as_text=True)

    type_resp = client.get("/admin/object-types/999999/edit", follow_redirects=True)
    assert type_resp.status_code == 200
    assert "Тип объекта не найден." in type_resp.get_data(as_text=True)


def test_sidebar_navigation_uses_svg_icons_and_no_product_contour(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Sidebar icons")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Сессии и аналитика" in html
    assert "Справочник" in html
    assert "Администрирование" in html
    assert '<svg viewBox="0 0 24 24" fill="none">' in html
    assert "M4 4h7v7H4z" in html
    assert "M5 4h12a2 2" in html
    assert "M12 3.5l2 .9" in html
    assert "Продуктовый контур" not in html
    assert "sidebar-promo" not in html


def test_legacy_analysis_pages_redirect_to_main_flow(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Legacy redirects")

    evaluation = client.get(f"/evaluation/{session_id}", follow_redirects=False)
    assert evaluation.status_code in (302, 303)
    assert evaluation.headers["Location"].endswith(f"/sessions/{session_id}")

    recommend = client.get(f"/recommend/{session_id}", follow_redirects=False)
    assert recommend.status_code in (302, 303)
    assert recommend.headers["Location"].endswith(f"/sessions/{session_id}")
