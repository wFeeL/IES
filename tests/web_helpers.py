from __future__ import annotations

from typing import Any, Dict


def login(client, username: str, password: str) -> None:
    resp = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "remember": "y",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def create_session(
    client, *, title: str = "Test Session", selected_strategy: str = "balanced"
) -> int:
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    resp = client.post(
        "/api/sessions",
        json={
            "title": title,
            "ruleset_id": ruleset_id,
            "selected_strategy": selected_strategy,
            "budget_total": 5000,
        },
    )
    assert resp.status_code == 200
    payload: Dict[str, Any] = resp.get_json()
    assert payload["ok"] is True
    return int(payload["item"]["id"])


def ruleset_id_by_code(client, code: str) -> int:
    resp = client.get("/api/rulesets")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    return int(next(row["id"] for row in items if row["code"] == code))
