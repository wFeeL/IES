from __future__ import annotations

import re

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import EvaluationResult, GameSession
from ies_bot_skeleton.web.services.seed import ensure_seed_data

from tests.web_helpers import create_session, login, ruleset_id_by_code


def _type_id_by_code(client, code: str) -> int:
    rows = client.get("/api/object-types").get_json()["items"]
    return int(next(row["id"] for row in rows if row["code"] == code))


def _h48_csv_house_market() -> bytes:
    rows = ["tick,wind,illumination,houseA,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{3 + (tick % 4)},{0.5 + (tick % 5) * 0.08:.2f},{9 + (tick % 3)},{10 + (tick % 4)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'window\.IES_CSRF_TOKEN = "([^"]+)"', html)
    assert match, "csrf token not found"
    return match.group(1)


def test_malformed_json_does_not_create_session(client, app):
    login(client, "admin", "admin123")

    with app.app_context():
        before = db.session.query(GameSession).count()

    resp = client.post("/api/sessions", data='{"bad"', content_type="application/json")
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"
    assert payload["error"]["message"] == "Некорректный JSON"

    with app.app_context():
        after = db.session.query(GameSession).count()
        assert after == before


def test_non_object_json_payload_is_rejected(client):
    login(client, "admin", "admin123")

    resp = client.post("/api/sessions", data="[]", content_type="application/json")
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"
    assert payload["error"]["message"] == "JSON payload должен быть объектом"


def test_malformed_json_does_not_persist_evaluation(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Malformed eval")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Eval lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    lot_id = int(lot_resp.get_json()["item"]["id"])

    with app.app_context():
        before = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).count()

    resp = client.post(
        f"/api/lots/{lot_id}/evaluate",
        data='{"bad"',
        content_type="application/json",
    )
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_request"

    with app.app_context():
        after = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).count()
        assert after == before


def test_list_lots_for_missing_session_returns_404(client):
    login(client, "admin", "admin123")

    resp = client.get("/api/lots?session_id=999999")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_admin_endpoints_return_404_for_missing_entities(client):
    login(client, "admin", "admin123")

    missing_ruleset = client.put("/api/rulesets/999999", json={"name": "x"})
    assert missing_ruleset.status_code == 404
    assert missing_ruleset.get_json()["error"]["code"] == "not_found"

    missing_pack = client.get("/api/start-pack-templates/999999")
    assert missing_pack.status_code == 404
    assert missing_pack.get_json()["error"]["code"] == "not_found"

    missing_type = client.put("/api/object-types/999999", json={"name": "x"})
    assert missing_type.status_code == 404
    assert missing_type.get_json()["error"]["code"] == "not_found"


def test_analyst_cannot_read_admin_api(client):
    login(client, "analyst", "analyst123")

    for path in (
        "/api/rulesets",
        "/api/start-pack-templates",
        "/api/start-pack-templates/1",
    ):
        resp = client.get(path)
        assert resp.status_code == 403
        payload = resp.get_json()
        assert payload["ok"] is False
        assert payload["error"]["code"] == "forbidden"


def test_object_crud_marks_session_evaluations_stale(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Object stale")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Object-sensitive lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    lot_id = int(lot_resp.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert rows
        assert all(not row.is_stale for row in rows)

    create_resp = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": wind_id,
            "custom_name": "Extra object",
        },
    )
    assert create_resp.status_code == 200
    object_id = int(create_resp.get_json()["item"]["id"])

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)
        for row in rows:
            row.is_stale = False
            row.stale_reason = ""
            db.session.add(row)
        db.session.commit()

    update_resp = client.put(
        f"/api/objects/{object_id}",
        json={
            "custom_name": "Extra object v2",
            "current_parameters": {"generation_mw": 11.0},
        },
    )
    assert update_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)
        for row in rows:
            row.is_stale = False
            row.stale_reason = ""
            db.session.add(row)
        db.session.commit()

    delete_resp = client.delete(f"/api/objects/{object_id}")
    assert delete_resp.status_code == 200

    with app.app_context():
        rows = db.session.query(EvaluationResult).filter_by(lot_id=lot_id).all()
        assert any(row.is_stale for row in rows)
        assert any("object_changed" in (row.stale_reason or "") for row in rows)


def test_unexpected_api_errors_return_json_payload(client, app, monkeypatch):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Internal error contract")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Crash lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    lot_id = int(lot_resp.get_json()["item"]["id"])

    from ies_bot_skeleton.web.routes import api_analysis as api_routes

    def _raise_internal(*args, **kwargs):
        raise RuntimeError("boom")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    monkeypatch.setattr(api_routes, "evaluate_session_lot", _raise_internal)

    resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert resp.status_code == 500
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["message"] == "Внутренняя ошибка API"


def test_forecast_compatibility_and_strategy_endpoints(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Strategy API")
    wind_id = _type_id_by_code(client, "wind")

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot A",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_a.status_code == 200
    lot_b = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Lot B",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_b.status_code == 200

    import io

    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "API H48",
            "file": (io.BytesIO(_h48_csv_house_market()), "h48.csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    forecast_id = int(upload.get_json()["item"]["id"])
    selected = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"selected_forecast_id": forecast_id},
    )
    assert selected.status_code == 200

    compat = client.get(f"/api/sessions/{session_id}/forecast-compatibility")
    assert compat.status_code == 200
    compat_item = compat.get_json()["item"]
    assert compat_item["session_id"] == session_id
    assert "compatibility_report" in compat_item
    assert "covered_types" in compat_item["compatibility_report"]

    strategy = client.get(f"/api/sessions/{session_id}/strategy")
    assert strategy.status_code == 200
    strategy_item = strategy.get_json()["item"]
    assert "best_singles" in strategy_item
    assert "best_pairs" in strategy_item
    assert "best_groups" in strategy_item
    assert "best_combination" in strategy_item
    if strategy_item["best_singles"]:
        assert "budget_limited_bid" in strategy_item["best_singles"][0]


def test_incompatible_forecast_blocks_evaluation_and_strategy(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Compatibility block")
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Blocked lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_resp.status_code == 200
    lot_id = int(lot_resp.get_json()["item"]["id"])

    import io

    short_upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Short forecast",
            "file": (
                io.BytesIO(
                    b"tick,wind,illumination,houseA,market_price\n0,3,0.6,10,12\n1,4,0.7,11,13\n"
                ),
                "short.csv",
            ),
        },
        content_type="multipart/form-data",
    )
    assert short_upload.status_code == 200
    short_forecast_id = int(short_upload.get_json()["item"]["id"])
    selected = client.put(
        f"/api/sessions/{session_id}/analysis-settings",
        json={"selected_forecast_id": short_forecast_id},
    )
    assert selected.status_code == 200

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 409
    eval_payload = eval_resp.get_json()
    assert eval_payload["error"]["code"] == "forecast_incompatible"
    assert "compatibility_report" in eval_payload["error"]["details"]

    analytics_resp = client.get(f"/api/sessions/{session_id}/lots/analytics")
    assert analytics_resp.status_code == 409
    assert analytics_resp.get_json()["error"]["code"] == "forecast_incompatible"

    strategy_resp = client.get(f"/api/sessions/{session_id}/strategy")
    assert strategy_resp.status_code == 409
    assert strategy_resp.get_json()["error"]["code"] == "forecast_incompatible"


def test_evaluate_and_analytics_return_uncapped_and_budget_limited_bids(client):
    login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    create_resp = client.post(
        "/api/sessions",
        json={
            "title": "Bid contract",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "budget_total": 12.0,
        },
    )
    assert create_resp.status_code == 200
    session_id = int(create_resp.get_json()["item"]["id"])
    wind_id = _type_id_by_code(client, "wind")

    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Budget pressure lot",
            "scope": "normal",
            "base_bid": 10,
            "current_bid": 10,
            "items": [{"object_type_id": wind_id, "quantity": 8}],
        },
    )
    assert lot_resp.status_code == 200
    lot_id = int(lot_resp.get_json()["item"]["id"])

    eval_resp = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert eval_resp.status_code == 200
    item = eval_resp.get_json()["item"]
    assert "budget_limited_bid" in item
    assert item["decision_summary"]["budget_limited_bid"] == pytest.approx(
        item["budget_limited_bid"]
    )
    assert item["decision_summary"]["budget_remaining"] == pytest.approx(
        item["portfolio_context"]["remaining_budget"]
    )
    assert item["hard_ceiling_bid"] >= item["target_bid"] >= item["cautious_bid"]
    assert item["budget_limited_bid"] == pytest.approx(
        min(item["target_bid"], item["portfolio_context"]["remaining_budget"])
    )

    analytics_resp = client.get(f"/api/sessions/{session_id}/lots/analytics")
    assert analytics_resp.status_code == 200
    row = next(
        entry for entry in analytics_resp.get_json()["items"] if int(entry["lot_id"]) == lot_id
    )
    assert row["budget_limited_bid"] == pytest.approx(item["budget_limited_bid"])
    assert row["target_bid"] == pytest.approx(item["target_bid"])


def test_api_returns_structured_csrf_error_for_recalculate(tmp_path):
    db_path = tmp_path / "csrf_contract.db"
    app = create_app("development")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}")

    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")
        client = app.test_client()
        try:
            login_page = client.get("/login")
            csrf_token = _extract_csrf(login_page.get_data(as_text=True))
            login_resp = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "admin123",
                    "remember": "y",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )
            assert login_resp.status_code in (302, 303)

            dashboard = client.get("/dashboard")
            api_csrf = _extract_csrf(dashboard.get_data(as_text=True))
            ruleset_id = ruleset_id_by_code(client, "ies_2026")

            session_resp = client.post(
                "/api/sessions",
                json={
                    "title": "CSRF session",
                    "ruleset_id": ruleset_id,
                    "selected_strategy": "balanced",
                    "budget_total": 500.0,
                },
                headers={"X-CSRFToken": api_csrf},
            )
            assert session_resp.status_code == 200
            session_id = int(session_resp.get_json()["item"]["id"])

            wind_id = next(
                int(row["id"])
                for row in client.get("/api/object-types").get_json()["items"]
                if row["code"] == "wind"
            )
            lot_resp = client.post(
                "/api/lots",
                json={
                    "session_id": session_id,
                    "name": "CSRF lot",
                    "scope": "normal",
                    "base_bid": 100.0,
                    "current_bid": 100.0,
                    "items": [{"object_type_id": wind_id, "quantity": 1}],
                },
                headers={"X-CSRFToken": api_csrf},
            )
            assert lot_resp.status_code == 200

            ok_resp = client.post(
                f"/api/sessions/{session_id}/recalculate",
                json={},
                headers={"X-CSRFToken": api_csrf},
            )
            assert ok_resp.status_code == 200

            bad_resp = client.post(
                f"/api/sessions/{session_id}/recalculate",
                json={},
                headers={"X-CSRFToken": "bad-token"},
            )
            assert bad_resp.status_code == 403
            bad_payload = bad_resp.get_json()
            assert bad_payload["ok"] is False
            assert bad_payload["error"]["code"] == "csrf_failed"

            missing_resp = client.post(f"/api/sessions/{session_id}/recalculate", json={})
            assert missing_resp.status_code == 403
            missing_payload = missing_resp.get_json()
            assert missing_payload["ok"] is False
            assert missing_payload["error"]["code"] == "csrf_failed"
        finally:
            db.session.remove()
            db.drop_all()
