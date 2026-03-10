from __future__ import annotations

from tests.web_helpers import create_session, login


def test_second_level_pages_have_back_breadcrumbs_and_cancel(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Nav")

    resp = client.get(f"/lots/{session_id}/edit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert f'href="/lots/{session_id}"' in html  # back fallback and cancel link target
    assert "Back" in html
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
