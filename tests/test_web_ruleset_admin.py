from __future__ import annotations

from tests.web_helpers import login


def _template_payload(code: str, name: str):
    return {
        "code": code,
        "name": name,
        "description": "template",
        "items": [
            {
                "row_key": "1",
                "object_type_code": "main_substation",
                "quantity": 1,
            },
            {
                "row_key": "2",
                "parent_key": "1",
                "object_type_code": "mini_substation_a",
                "quantity": 1,
            },
        ],
    }


def test_ruleset_versioning_allows_multiple_active_rulesets(client):
    login(client, "admin", "admin123")

    template_resp = client.post(
        "/api/start-pack-templates", json=_template_payload("pack_ruleset", "Pack")
    )
    assert template_resp.status_code == 200
    template_id = int(template_resp.get_json()["item"]["id"])

    create_resp = client.post(
        "/api/rulesets",
        json={
            "code": "ies_test",
            "name": "Ruleset Test",
            "config_json": {"x": 1},
            "model_settings": {"evaluation": {"risk_lambda": 0.33}},
            "active_start_pack_template_id": template_id,
            "is_active": True,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.get_json()["item"]
    created_id = int(created["id"])
    assert created["active_start_pack_template_id"] == template_id
    assert "model_settings" in created

    list_resp = client.get("/api/rulesets")
    assert list_resp.status_code == 200
    rulesets = list_resp.get_json()["items"]
    active_ids = [int(r["id"]) for r in rulesets if r["is_active"]]
    assert created_id in active_ids

    copy_resp = client.post(f"/api/rulesets/{created_id}/copy", json={"name": "Ruleset Test Copy"})
    assert copy_resp.status_code == 200
    copied = copy_resp.get_json()["item"]
    assert copied["code"] == "ies_test"
    assert copied["version"] != created["version"]

    activate_copy = client.post(f"/api/rulesets/{copied['id']}/activate", json={})
    assert activate_copy.status_code == 200
    rulesets_after = client.get("/api/rulesets").get_json()["items"]
    active_after = [int(r["id"]) for r in rulesets_after if r["is_active"]]
    assert created_id in active_after
    assert int(copied["id"]) in active_after


def test_ruleset_cannot_deactivate_last_active(client):
    login(client, "admin", "admin123")

    rulesets = client.get("/api/rulesets").get_json()["items"]
    active_rulesets = [row for row in rulesets if row["is_active"]]
    assert len(active_rulesets) >= 2

    for row in active_rulesets[:-1]:
        resp = client.post(f"/api/rulesets/{row['id']}/deactivate", json={})
        assert resp.status_code == 200

    active = active_rulesets[-1]

    resp = client.post(f"/api/rulesets/{active['id']}/deactivate", json={})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
