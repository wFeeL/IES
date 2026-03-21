from __future__ import annotations

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.services.seed import ensure_seed_data


def _expect(status_code: int, allowed: tuple[int, ...], *, endpoint: str) -> None:
    if status_code not in allowed:
        raise SystemExit(
            f"Smoke check failed for {endpoint}: status {status_code}, expected one of {allowed}"
        )


def main() -> None:
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")
        client = app.test_client()

        _expect(client.get("/login").status_code, (200,), endpoint="GET /login")
        _expect(
            client.post(
                "/login",
                data={"username": "admin", "password": "admin123", "remember": "y"},
                follow_redirects=False,
            ).status_code,
            (302, 303),
            endpoint="POST /login",
        )
        _expect(client.get("/dashboard").status_code, (200,), endpoint="GET /dashboard")
        _expect(client.get("/catalog").status_code, (200,), endpoint="GET /catalog")

        rulesets = client.get("/api/rulesets").get_json()["items"]
        ruleset_id = int(next(row["id"] for row in rulesets if row["code"] == "ies_2026"))
        created = client.post(
            "/api/sessions",
            json={
                "title": "Smoke Session",
                "ruleset_id": ruleset_id,
                "selected_strategy": "balanced",
                "budget_total": 250.0,
            },
        )
        _expect(created.status_code, (200,), endpoint="POST /api/sessions")
        session_id = int(created.get_json()["item"]["id"])

        _expect(
            client.post(f"/api/sessions/{session_id}/recalculate", json={"include_strategy": False}).status_code,
            (200,),
            endpoint="POST /api/sessions/<id>/recalculate",
        )
        _expect(
            client.get(f"/api/sessions/{session_id}/lots/analytics").status_code,
            (200,),
            endpoint="GET /api/sessions/<id>/lots/analytics",
        )
        _expect(
            client.get(f"/api/sessions/{session_id}/auction/events").status_code,
            (200,),
            endpoint="GET /api/sessions/<id>/auction/events",
        )

        _expect(client.get(f"/sessions/{session_id}").status_code, (200,), endpoint="GET /sessions/<id>")
        _expect(client.get(f"/quick-auction/{session_id}").status_code, (200,), endpoint="GET /quick-auction/<id>")
        _expect(client.get(f"/lots/{session_id}").status_code, (200,), endpoint="GET /lots/<id>")

        db.session.remove()
        db.drop_all()

    print("web smoke ok")


if __name__ == "__main__":
    main()
