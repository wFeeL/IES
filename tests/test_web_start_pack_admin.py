from __future__ import annotations

from tests.web_helpers import create_session, login


def _start_pack_items():
    return [
        {"row_key": "1", "object_type_code": "main_substation", "quantity": 1, "custom_name": "Main"},
        {
            "row_key": "2",
            "parent_key": "1",
            "object_type_code": "mini_substation_a",
            "quantity": 1,
            "custom_name": "Mini",
        },
        {
            "row_key": "3",
            "parent_key": "2",
            "object_type_code": "cyber_solar",
            "quantity": 1,
            "custom_name": "Solar",
        },
        {
            "row_key": "4",
            "parent_key": "2",
            "object_type_code": "house",
            "quantity": 1,
            "custom_name": "House",
        },
    ]


def test_analyst_cannot_modify_start_pack_templates(client):
    login(client, "analyst", "analyst123")
    resp = client.post(
        "/api/start-pack-templates",
        json={"code": "forbidden_pack", "name": "Forbidden", "items": _start_pack_items()},
    )
    assert resp.status_code == 403


def test_admin_start_pack_crud_and_one_time_apply(client):
    login(client, "admin", "admin123")

    create_template = client.post(
        "/api/start-pack-templates",
        json={
            "code": "custom_pack",
            "name": "Custom Pack",
            "description": "pack",
            "items": _start_pack_items(),
        },
    )
    assert create_template.status_code == 200
    template = create_template.get_json()["item"]

    create_ruleset = client.post(
        "/api/rulesets",
        json={
            "code": "ies_pack",
            "name": "Pack Ruleset",
            "config_json": {},
            "model_settings": {},
            "active_start_pack_template_id": template["id"],
            "is_active": True,
        },
    )
    assert create_ruleset.status_code == 200
    ruleset_id = int(create_ruleset.get_json()["item"]["id"])

    session_resp = client.post(
        "/api/sessions",
        json={"title": "Pack Session", "ruleset_id": ruleset_id, "selected_strategy": "balanced"},
    )
    assert session_resp.status_code == 200
    session_id = int(session_resp.get_json()["item"]["id"])

    apply_resp = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert apply_resp.status_code == 200
    assert len(apply_resp.get_json()["created"]) == 4

    apply_again_resp = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert apply_again_resp.status_code == 400
    assert apply_again_resp.get_json()["ok"] is False

    # Admin SSR page exists.
    page_resp = client.get("/settings/start-packs")
    assert page_resp.status_code == 200
    assert "Шаблоны стартового пакета" in page_resp.get_data(as_text=True)



def test_analyst_cannot_open_admin_start_pack_pages(client):
    login(client, "analyst", "analyst123")
    resp = client.get("/settings/start-packs")
    assert resp.status_code == 403
