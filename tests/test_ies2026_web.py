from __future__ import annotations

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import GameSession, Lot, LotItem, ObjectType

from tests.web_helpers import create_session, login, ruleset_id_by_code


def test_session_pages_render_2026_navigation(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="IES 2026 UI")

    start_pack = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert start_pack.status_code == 200

    session_html = client.get(f"/sessions/{session_id}").get_data(as_text=True)
    lots_html = client.get(f"/lots/{session_id}").get_data(as_text=True)
    forecast_html = client.get(f"/forecast/{session_id}").get_data(as_text=True)
    system_html = client.get(f"/system/{session_id}").get_data(as_text=True)

    assert "Объекты" in session_html
    assert "Аукцион и лоты" in session_html
    assert "Прогнозы" in session_html
    assert "Консолидированная оценка лотов" in lots_html
    assert "Прогноз ИЭС 2026" in forecast_html
    assert "Валидация сети 2026" in system_html


def test_lot_detail_marks_topology_risk_as_non_recommendable(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Topology risk")

    with app.app_context():
        session = db.session.get(GameSession, session_id)
        wind_type = db.session.query(ObjectType).filter_by(code="wind").one()
        lot = Lot(
            session_id=session_id,
            name="Risky wind lot",
            scope="normal",
            status="available",
            base_bid=5.0,
            current_bid=5.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=wind_type.id, quantity=1))
        db.session.commit()
        lot_id = int(lot.id)
        assert session is not None

    html = client.get(f"/lots/item/{lot_id}").get_data(as_text=True)
    assert "Topology risk блокирует рекомендацию." in html
    assert "В системе отсутствует главная подстанция." in html


def test_strategy_selection_and_post_auction_plan_export_work(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Strategy and plan")

    strategy_switch = client.post(
        f"/sessions/{session_id}/strategy-selection",
        data={"selected_strategy": "storage"},
        follow_redirects=False,
    )
    assert strategy_switch.status_code in (302, 303)

    session_payload = client.get(f"/api/sessions/{session_id}").get_json()["item"]
    assert session_payload["selected_strategy"] == "unified"

    json_plan = client.get(f"/api/sessions/{session_id}/post-auction-plan")
    assert json_plan.status_code == 200
    plan_item = json_plan.get_json()["item"]
    assert "topology_candidates" in plan_item
    assert "market_plan" in plan_item
    assert "installation_priority" in plan_item
    assert plan_item["game"]["analysis_stage"] == "post_auction_system_planning"

    yaml_plan = client.get(f"/api/sessions/{session_id}/post-auction-plan.yaml")
    assert yaml_plan.status_code == 200
    yaml_text = yaml_plan.get_data(as_text=True)
    assert "market_plan:" in yaml_text
    assert "installation_priority:" in yaml_text
    assert "tick_model:" in yaml_text
    assert "stage_note:" in yaml_text


def test_main_user_flow_hides_strategy_selector_and_uses_unified_optimizer(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Unified UI")

    dashboard_html = client.get("/dashboard").get_data(as_text=True)
    session_html = client.get(f"/sessions/{session_id}").get_data(as_text=True)

    assert "sessionStrategySelect" not in dashboard_html
    assert "/strategy-selection" not in session_html
    assert "Единый оптимизатор" in dashboard_html
    assert "Единый оптимизатор" in session_html


def test_strategy_endpoint_returns_unified_catalog_with_groups(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Unified catalog")

    type_rows = client.get("/api/object-types").get_json()["items"]
    type_map = {row["code"]: int(row["id"]) for row in type_rows}
    for idx, code in enumerate(("wind", "solar", "house", "office"), start=1):
        resp = client.post(
            "/api/lots",
            json={
                "session_id": session_id,
                "name": f"Catalog lot {idx}",
                "scope": "normal",
                "base_bid": 6.0 + idx,
                "current_bid": 6.0 + idx,
                "items": [{"object_type_id": type_map[code], "quantity": 1}],
            },
        )
        assert resp.status_code == 200

    response = client.get(f"/api/sessions/{session_id}/strategy?top_n=10&force=1")
    assert response.status_code == 200
    item = response.get_json()["item"]

    assert item["strategy"] == "unified"
    assert item["analysis_mode"] == "unified_optimizer"
    assert "best_singles" in item and "best_pairs" in item and "best_groups" in item
    assert item["best_groups"]
    profits = [float(row["risk_adjusted_profit"]) for row in item["best_groups"]]
    assert profits == sorted(profits, reverse=True)
    first_group = item["best_groups"][0]
    assert "optimal_purchase_price_total" in first_group
    assert "expected_profit" in first_group
    assert "topology_feasibility" in first_group
    assert "wind_uncertainty_penalty" in first_group


def test_lot_detail_shows_stage_split_and_wind_uncertainty(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Wind detail")

    with app.app_context():
        wind_type = db.session.query(ObjectType).filter_by(code="wind").one()
        lot = Lot(
            session_id=session_id,
            name="Wind detail lot",
            scope="global",
            status="available",
            base_bid=8.0,
            current_bid=8.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=wind_type.id, quantity=1))
        db.session.commit()
        lot_id = int(lot.id)

    html = client.get(f"/lots/item/{lot_id}").get_data(as_text=True)
    assert "Stage A" in html
    assert "Wind uncertainty penalty" in html
    assert "post-auction" in html.lower()


def test_special_case_allpay_only_applies_when_explicitly_triggered(client):
    login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    created = client.post(
        "/api/sessions",
        json={
            "title": "Special all-pay",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "budget_total": 5200.0,
        },
    )
    assert created.status_code == 200
    session_id = int(created.get_json()["item"]["id"])

    type_rows = client.get("/api/object-types").get_json()["items"]
    wind_id = next(int(row["id"]) for row in type_rows if row["code"] == "wind")

    ordinary = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Ordinary wind",
            "scope": "global",
            "base_bid": 30.0,
            "current_bid": 30.0,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert ordinary.status_code == 200
    ordinary_lot_id = int(ordinary.get_json()["item"]["id"])

    ordinary_bid = client.post(
        f"/api/sessions/{session_id}/auction/actions",
        json={
            "lot_id": ordinary_lot_id,
            "action": "bid",
            "bid_amount": 95.0,
            "auction_mode": "ordinary_tariff_auction",
            "allpay_triggered": False,
        },
    )
    assert ordinary_bid.status_code == 200
    ordinary_event = ordinary_bid.get_json()["item"]["event"]
    ordinary_lost = client.post(
        f"/api/sessions/{session_id}/auction/outcomes",
        json={
            "lot_id": ordinary_lot_id,
            "event_id": int(ordinary_event["id"]),
            "outcome": "lost",
        },
    )
    assert ordinary_lost.status_code == 200

    after_ordinary = client.get(f"/api/sessions/{session_id}").get_json()["item"]
    assert float(after_ordinary["allpay_spent"]) == 0.0

    tie_break = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Tie-break wind",
            "scope": "global",
            "base_bid": 30.0,
            "current_bid": 30.0,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert tie_break.status_code == 200
    tie_break_lot_id = int(tie_break.get_json()["item"]["id"])

    special_bid = client.post(
        f"/api/sessions/{session_id}/auction/actions",
        json={
            "lot_id": tie_break_lot_id,
            "action": "bid",
            "bid_amount": 125.0,
            "auction_mode": "tie_break_all_pay",
            "allpay_triggered": True,
        },
    )
    assert special_bid.status_code == 200
    special_event = special_bid.get_json()["item"]["event"]
    special_lost = client.post(
        f"/api/sessions/{session_id}/auction/outcomes",
        json={
            "lot_id": tie_break_lot_id,
            "event_id": int(special_event["id"]),
            "outcome": "lost",
        },
    )
    assert special_lost.status_code == 200

    after_special = client.get(f"/api/sessions/{session_id}").get_json()["item"]
    assert float(after_special["allpay_spent"]) == 125.0


def test_special_case_allpay_respects_budget_cap(client):
    login(client, "admin", "admin123")
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    created = client.post(
        "/api/sessions",
        json={
            "title": "All-pay cap",
            "ruleset_id": ruleset_id,
            "selected_strategy": "balanced",
            "budget_total": 7000.0,
        },
    )
    assert created.status_code == 200
    session_id = int(created.get_json()["item"]["id"])

    type_rows = client.get("/api/object-types").get_json()["items"]
    wind_id = next(int(row["id"]) for row in type_rows if row["code"] == "wind")
    lot_resp = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Fixed package",
            "scope": "global",
            "base_bid": 30.0,
            "current_bid": 30.0,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )
    assert lot_resp.status_code == 200
    lot_id = int(lot_resp.get_json()["item"]["id"])

    bid_resp = client.post(
        f"/api/sessions/{session_id}/auction/actions",
        json={
            "lot_id": lot_id,
            "action": "bid",
            "bid_amount": 5100.0,
            "auction_mode": "fixed_tariff_package_all_pay",
            "allpay_triggered": True,
        },
    )
    assert bid_resp.status_code == 400
    error = bid_resp.get_json()["error"]
    assert error["code"] in {"validation_error", "bad_request"}
