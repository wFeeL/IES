from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import GameSession, ObjectInstance
from ies_bot_skeleton.web.services.formatting import format_number
from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _h48_csv_with_raw_columns() -> bytes:
    rows = ["tick,wind,illumination,house,office,factory,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{3 + (tick % 4)},{0.4 + (tick % 5) * 0.1:.2f},{9 + (tick % 3)},{6 + (tick % 2)},{5 + (tick % 4)},{10 + (tick % 4)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _upload_and_select_forecast(client, session_id: int) -> int:
    upload = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": "Raw names H48",
            "file": (io.BytesIO(_h48_csv_with_raw_columns()), "raw_h48.csv"),
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
    return forecast_id


def test_number_formatter_strips_integer_fraction():
    assert format_number(200.0, 1) == "200"
    assert format_number(150.0, 2) == "150"
    assert format_number(48.0, 3) == "48"
    assert format_number(48.125, 2) == "48.13"


def test_sidebar_and_profile_icons_use_svg(client):
    login(client, "admin", "admin123")
    create_session(client, title="Icon check")

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert '<span class="sidebar-brand-mark"' in html
    assert "user-chip-avatar" in html
    assert "<svg" in html
    assert "◉" not in html
    assert "◌" not in html


def test_active_forecast_uses_raw_csv_columns_and_zero_tick_range(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Raw forecast names")
    forecast_id = _upload_and_select_forecast(client, session_id)

    forecast_page = client.get(f"/forecast/{session_id}")
    assert forecast_page.status_code == 200
    forecast_html = forecast_page.get_data(as_text=True)
    assert "0–47 (48 периодов)" in forecast_html
    assert "Горизонт" in forecast_html
    assert "Периодов" not in forecast_html
    assert "house" in forecast_html
    assert "office" in forecast_html
    assert "factory" in forecast_html
    assert "class3" not in forecast_html
    assert "data-forecast-id=" in forecast_html
    assert 'class="forecast-columns-grid mt-4"' in forecast_html
    assert 'class="mapping-list mt-2"' in forecast_html

    forecast_js = client.get("/static/js/analysis/forecast_center.js").get_data(as_text=True)
    assert "async function runDiagnostics(forecastId, withChart)" in forecast_js
    assert "setSelectedForecast(forecastId);" in forecast_js
    assert "mapped_raw_stats_display" in forecast_js

    forecasts = client.get(f"/api/forecast/{forecast_id}")
    assert forecasts.status_code == 200
    summary = forecasts.get_json()["item"]["summary"]
    assert summary["raw_csv_columns"] == ["tick", "wind", "illumination", "house", "office", "factory", "market_price"]
    assert "house" in list(summary.get("used_raw_columns") or [])
    assert list(summary.get("unsupported_raw_columns") or []) == []
    mapping_rows = list(summary.get("column_mapping_rows") or [])
    assert any(str(row.get("raw_name")) == "house" and str(row.get("canonical_key")) == "house_load" for row in mapping_rows)
    mapped_columns = list(summary.get("mapped_raw_columns") or [])
    assert "class3" not in mapped_columns
    stats_rows = list(summary.get("mapped_raw_stats_display") or [])
    labels = [str(row.get("label")) for row in stats_rows]
    assert len(labels) == len(set(labels))

    workbench = client.get(f"/sessions/{session_id}")
    assert workbench.status_code == 200
    workbench_html = workbench.get_data(as_text=True)
    assert "Горизонт" in workbench_html
    assert "Периодов" not in workbench_html
    assert "house" in workbench_html
    assert "office" in workbench_html
    assert "factory" in workbench_html
    assert "class3" not in workbench_html


def test_strategy_snapshot_has_titles_and_per_lot_prices(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Strategy format")
    tmap = _type_map(client)

    for idx, object_code in enumerate(("wind", "solar", "house"), start=1):
        resp = client.post(
            "/api/lots",
            json={
                "session_id": session_id,
                "name": f"Combo lot {idx}",
                "scope": "normal",
                "base_bid": 80 + idx * 5,
                "current_bid": 80 + idx * 5,
                "items": [{"object_type_id": tmap[object_code], "quantity": 1}],
            },
        )
        assert resp.status_code == 200

    _upload_and_select_forecast(client, session_id)
    strategy = client.get(f"/api/sessions/{session_id}/strategy?top_n=10")
    assert strategy.status_code == 200
    item = strategy.get_json()["item"]

    rows = list(item.get("best_singles") or []) + list(item.get("best_pairs") or [])
    rows += list(item.get("best_groups") or [])
    assert rows
    assert "scenarios" in item
    assert "full_budget" in item["scenarios"]
    assert "after_purchase" in item["scenarios"]
    assert "after_loss" in item["scenarios"]
    for scenario_key in ("full_budget", "after_purchase", "after_loss"):
        scenario = item["scenarios"][scenario_key]
        for bucket in ("best_singles", "best_pairs", "best_groups"):
            for row in scenario.get(bucket) or []:
                assert float(row["working_bid"]) > 0.0
        if scenario.get("best_combination") is not None:
            assert float(scenario["best_combination"]["working_bid"]) > 0.0
    strategy_js = client.get("/static/js/analysis/strategy_snapshot.js").get_data(as_text=True)
    assert "общая цена:" in strategy_js
    assert "общая прибыль:" in strategy_js
    assert "прибыль:" in strategy_js
    assert "strategy-lot-list" in strategy_js
    assert "Plan B" in strategy_js
    assert "После покупки" in strategy_js
    assert "ставить до:" not in strategy_js
    assert "Название группы -" not in strategy_js
    for row in rows:
        assert re.search(r"\(\d+\)", row["display_title"])
        assert "working_bid" in row
        assert "budget_adjusted_bid" in row
        assert "budget_fit" in row
        breakdown_rows = list(row.get("lot_bid_breakdown") or [])
        if len(row.get("lot_ids") or []) == 1 and breakdown_rows:
            part = breakdown_rows[0]
            assert float(part.get("recommended_bid") or 0.0) == pytest.approx(
                float(row.get("working_bid") or 0.0)
            )
            assert float(part.get("standalone_working_bid") or 0.0) == pytest.approx(
                float(row.get("working_bid") or 0.0)
            )
        if len(row.get("lot_ids") or []) >= 2 and breakdown_rows:
            assert sum(float(part.get("recommended_bid") or 0.0) for part in breakdown_rows) == pytest.approx(
                float(row.get("working_bid") or 0.0)
            )
        for breakdown in breakdown_rows:
            assert "budget_adjusted_bid" in breakdown
            assert "allocated_net_profit" in breakdown
            assert "allocated_working_bid" in breakdown
            assert "standalone_working_bid" in breakdown
            assert "recommended_bid" in breakdown
            assert "price" in breakdown
            assert "profit" in breakdown


def test_lot_evaluation_accounts_for_connection_sectors_when_points_differ(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Sector-agnostic lots")
    tmap = _type_map(client)

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "TPS+Office D/C",
            "scope": "normal",
            "base_bid": 70,
            "current_bid": 70,
            "items": [
                {
                    "object_type_id": tmap["tps"],
                    "quantity": 1,
                    "overrides": {"connection_point": "D"},
                },
                {
                    "object_type_id": tmap["office"],
                    "quantity": 1,
                    "overrides": {"connection_point": "C"},
                },
            ],
        },
    )
    assert lot_a.status_code == 200
    lot_a_id = int(lot_a.get_json()["item"]["id"])

    lot_b = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "TPS+Office A/A",
            "scope": "normal",
            "base_bid": 70,
            "current_bid": 70,
            "items": [
                {
                    "object_type_id": tmap["tps"],
                    "quantity": 1,
                    "overrides": {"connection_point": "A"},
                },
                {
                    "object_type_id": tmap["office"],
                    "quantity": 1,
                    "overrides": {"connection_point": "A"},
                },
            ],
        },
    )
    assert lot_b.status_code == 200
    lot_b_id = int(lot_b.get_json()["item"]["id"])

    eval_a = client.post(f"/api/lots/{lot_a_id}/evaluate", json={})
    eval_b = client.post(f"/api/lots/{lot_b_id}/evaluate", json={})
    assert eval_a.status_code == 200
    assert eval_b.status_code == 200
    row_a = eval_a.get_json()["item"]
    row_b = eval_b.get_json()["item"]

    assert float(row_a["summary_score"]) > float(row_b["summary_score"])
    assert float(row_a["working_bid"]) > float(row_b["working_bid"])
    assert float(row_a["financial_breakdown"]["result"]["net_profit"]) > float(
        row_b["financial_breakdown"]["result"]["net_profit"]
    )
    assert float(row_a["financial_breakdown"]["losses_and_risks"]["risk_total"]) >= 0.0
    assert float(row_b["financial_breakdown"]["losses_and_risks"]["risk_total"]) >= 0.0
    assert float(row_a["system_check"]["system_fit_score"]) >= float(row_b["system_check"]["system_fit_score"])
    assert "A/B/C" not in str(row_a["system_check"]["message"])
    assert "A/B/C" not in str(row_b["system_check"]["message"])
    for row in (row_a, row_b):
        for item in row["system_check"]["items"]:
            assert "current_point" in item
            assert "recommended_point" in item


def test_risk_metric_is_non_zero_and_scenario_texts_differ(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Risk check")
    tmap = _type_map(client)

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Risk lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [
                {"object_type_id": tmap["wind"], "quantity": 1},
                {"object_type_id": tmap["house"], "quantity": 1},
            ],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    evaluation = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert evaluation.status_code == 200
    payload = evaluation.get_json()["item"]
    risk_total = float(payload["financial_breakdown"]["losses_and_risks"]["risk_total"])
    assert risk_total > 0.0

    breakdown = payload["scenario_breakdown"]
    explanations = {
        str(breakdown["worst"]["explanation"]),
        str(breakdown["base"]["explanation"]),
        str(breakdown["best"]["explanation"]),
    }
    assert len(explanations) == 3


def test_quick_auction_buys_by_field_price_without_confirm_link(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Quick buy no confirm")
    tmap = _type_map(client)

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Quick lot",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    quick = client.get(f"/quick-auction/{session_id}")
    assert quick.status_code == 200
    quick_html = quick.get_data(as_text=True)
    assert f"/lots/item/{lot_id}/buy" not in quick_html
    assert "Цена покупки (по умолчанию — рабочая цена)" in quick_html
    assert 'id="purchasePriceInput"' in quick_html

    script = client.get("/static/js/analysis/quick_auction.js").get_data(as_text=True)
    assert "/api/lots/${lotId}/buy" in script
    assert "/api/sessions/${cfg().sessionId}/recalculate" in script
    assert "row.working_bid" in script
    assert "purchasePriceInput" in script

    session_payload = client.get(f"/api/sessions/{session_id}").get_json()["item"]
    budget_before = float(session_payload["budget_total"])

    buy = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 77.7})
    assert buy.status_code == 200
    item = buy.get_json()["item"]
    assert float(item["purchase_price"]) == pytest.approx(77.7)
    assert float(item["remaining_budget"]) == pytest.approx(budget_before - 77.7)
    assert int(item["available_lots_count"]) >= 0
    assert bool(item["refresh_required"]) is True

    recalc = client.post(f"/api/sessions/{session_id}/recalculate", json={})
    assert recalc.status_code == 200
    meta = recalc.get_json()["meta"]
    assert "shortlist_suggested_ids" in meta
    assert lot_id not in list(meta["shortlist_suggested_ids"] or [])


def test_buy_api_returns_full_post_buy_refresh_for_remaining_lots_and_strategy(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Buy refresh payload")
    tmap = _type_map(client)

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Refresh lot A",
            "scope": "normal",
            "base_bid": 80,
            "current_bid": 80,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot_a.status_code == 200
    lot_a_id = int(lot_a.get_json()["item"]["id"])

    lot_b = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Refresh lot B",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": tmap["solar"], "quantity": 1}],
        },
    )
    assert lot_b.status_code == 200
    lot_b_id = int(lot_b.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    buy = client.post(f"/api/lots/{lot_a_id}/buy", json={"purchase_price": 55.0})
    assert buy.status_code == 200
    payload = buy.get_json()
    assert payload["ok"] is True
    assert float(payload["item"]["purchase_price"]) == pytest.approx(55.0)
    refresh = payload.get("refresh") or {}
    meta = refresh.get("meta") or {}
    assert float(meta.get("spent_total", 0.0)) == pytest.approx(55.0)
    assert float(meta.get("remaining_budget", 0.0)) == pytest.approx(
        float(payload["item"]["remaining_budget"])
    )
    assert int(meta.get("bought_lots_count", 0)) == 1
    assert lot_a_id not in list(meta.get("shortlist_suggested_ids") or [])

    refreshed_rows = list(refresh.get("items") or [])
    remaining_row = next(
        row for row in refreshed_rows if int(row.get("lot_id") or 0) == lot_b_id
    )
    expected_remaining = float(payload["item"]["remaining_budget"])
    assert float((remaining_row.get("decision_summary") or {}).get("budget_remaining", 0.0)) == pytest.approx(
        expected_remaining
    )
    assert float((remaining_row.get("portfolio_context") or {}).get("remaining_budget", 0.0)) == pytest.approx(
        expected_remaining
    )
    assert float((remaining_row.get("metrics") or {}).get("portfolio_delta", {}).get("net_profit_base", 0.0)) == pytest.approx(
        float((remaining_row.get("scenario_breakdown") or {}).get("base", {}).get("net_profit", 0.0))
    )

    strategy = refresh.get("strategy") or {}
    assert "scenarios" in strategy
    assert "full_budget" in strategy["scenarios"]
    assert "after_purchase" in strategy["scenarios"]
    assert "after_loss" in strategy["scenarios"]
    assert float((strategy.get("portfolio_context") or {}).get("remaining_budget", 0.0)) == pytest.approx(
        expected_remaining
    )


def test_undo_buy_api_returns_full_recalculation_snapshot(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Undo refresh payload")
    tmap = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Undo refresh lot",
            "scope": "normal",
            "base_bid": 70,
            "current_bid": 70,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    buy = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 45.0})
    assert buy.status_code == 200
    budget_total = float((buy.get_json()["item"] or {}).get("budget_total", 0.0))

    undo = client.post(f"/api/lots/{lot_id}/undo-buy", json={})
    assert undo.status_code == 200
    payload = undo.get_json()
    refresh = payload.get("refresh") or {}
    meta = refresh.get("meta") or {}
    assert float(meta.get("spent_total", -1.0)) == pytest.approx(0.0)
    assert float(meta.get("remaining_budget", -1.0)) == pytest.approx(budget_total)
    assert int(meta.get("bought_lots_count", -1)) == 0
    rows = list(refresh.get("items") or [])
    restored_row = next(row for row in rows if int(row.get("lot_id") or 0) == lot_id)
    assert float((restored_row.get("decision_summary") or {}).get("budget_remaining", 0.0)) == pytest.approx(
        budget_total
    )


def test_quick_auction_evaluate_does_not_mutate_market_bid(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Quick eval no market mutation")
    tmap = _type_map(client)

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Quick eval lot",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 111,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    before_lot = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    before_bid = float(before_lot["current_bid"])

    evaluate = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert evaluate.status_code == 200

    after_eval = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert float(after_eval["current_bid"]) == pytest.approx(before_bid)

    buy = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 77.7})
    assert buy.status_code == 200
    lot_after_buy = client.get(f"/api/lots/{lot_id}").get_json()["item"]
    assert float(lot_after_buy["current_bid"]) == pytest.approx(before_bid)

    script = client.get("/static/js/analysis/quick_auction.js").get_data(as_text=True)
    assert "body: JSON.stringify({current_bid: bid})" not in script
    assert "apiFetchJson(`/api/lots/${lotId}`, {" not in script
    assert "function syncPurchasePriceInput" in script


def test_system_objects_sorted_latest_first_with_connection_recommendation(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="System sort and recommendation")
    wind_id = _type_map(client)["wind"]

    first = client.post(
        "/api/objects",
        json={"session_id": session_id, "object_type_id": wind_id, "custom_name": "Object A"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/objects",
        json={"session_id": session_id, "object_type_id": wind_id, "custom_name": "Object B"},
    )
    assert second.status_code == 200

    resp = client.get(f"/system/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Рекомендация подключения" in html
    assert html.find("Object B") < html.find("Object A")
    assert (
        "Рекомендуем точку" in html
        or "близко к оптимальному" in html
        or "Подключение не рекомендовано" in html
        or "Эффективная точка подключения не найдена" in html
    )


def test_working_price_consistent_between_detail_and_buy_form(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Working price consistency")
    tmap = _type_map(client)

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Working lot",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 100,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    evaluation = client.post(f"/api/lots/{lot_id}/evaluate", json={}).get_json()["item"]
    working = float(evaluation.get("working_bid") or 0.0)
    working_label = format_number(working, 1)

    detail_html = client.get(f"/lots/item/{lot_id}").get_data(as_text=True)
    assert working_label in detail_html

    buy_html = client.get(f"/lots/item/{lot_id}/buy").get_data(as_text=True)
    match = re.search(r'name="purchase_price"[^>]*value="([^"]+)"', buy_html)
    assert match
    assert float(match.group(1)) == pytest.approx(working)


def test_lot_detail_marks_scenario_bid_as_non_operational_metric(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Scenario bid label")
    tmap = _type_map(client)

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Scenario label lot",
            "scope": "normal",
            "base_bid": 95,
            "current_bid": 95,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    html = client.get(f"/lots/item/{lot_id}").get_data(as_text=True)
    assert "Сценарный потолок ставки (не цена покупки)" in html
    assert "Рабочая ставка сценария" not in html


def test_session_api_exposes_budget_snapshot_and_updates_after_purchase(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Session budget snapshot")
    tmap = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Budget lot",
            "scope": "normal",
            "base_bid": 60,
            "current_bid": 60,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    before = client.get(f"/api/sessions/{session_id}")
    assert before.status_code == 200
    before_item = before.get_json()["item"]
    assert float(before_item["spent_total"]) == pytest.approx(0.0)
    assert int(before_item["bought_lots_count"]) == 0

    buy = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 55.0})
    assert buy.status_code == 200

    after = client.get(f"/api/sessions/{session_id}")
    assert after.status_code == 200
    after_item = after.get_json()["item"]
    assert float(after_item["spent_total"]) == pytest.approx(55.0)
    assert float(after_item["remaining_budget"]) == pytest.approx(float(after_item["budget_total"]) - 55.0)
    assert int(after_item["bought_lots_count"]) == 1


def test_workbench_and_forecast_pages_share_current_budget_snapshot_after_purchase(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Budget sync SSR")
    tmap = _type_map(client)

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Budget sync lot",
            "scope": "normal",
            "base_bid": 70,
            "current_bid": 70,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert created.status_code == 200
    lot_id = int(created.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    buy = client.post(f"/api/lots/{lot_id}/buy", json={"purchase_price": 55.0})
    assert buy.status_code == 200
    snapshot = buy.get_json()["item"]
    spent_label = format_number(snapshot["spent_total"], 1)
    remaining_label = format_number(snapshot["remaining_budget"], 1)

    workbench_html = client.get(f"/sessions/{session_id}").get_data(as_text=True)
    forecast_html = client.get(f"/forecast/{session_id}").get_data(as_text=True)

    assert f"Потрачено: <span data-session-spent-total>{spent_label}</span>" in workbench_html
    assert f"Остаток: <span data-session-remaining-budget>{remaining_label}</span>" in workbench_html
    assert f"Потрачено: <span data-session-spent-total>{spent_label}</span>" in forecast_html
    assert f"Остаток: <span data-session-remaining-budget>{remaining_label}</span>" in forecast_html


def test_recalculate_meta_contains_shortlist_and_multiple_non_zero_working_bids(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Recalc shortlist meta")
    tmap = _type_map(client)

    for idx, code in enumerate(("wind", "solar", "wind"), start=1):
        resp = client.post(
            "/api/lots",
            json={
                "session_id": session_id,
                "name": f"Meta lot {idx}",
                "scope": "normal",
                "base_bid": 40 + idx * 5,
                "current_bid": 40 + idx * 5,
                "items": [{"object_type_id": tmap[code], "quantity": 1}],
            },
        )
        assert resp.status_code == 200
    _upload_and_select_forecast(client, session_id)

    recalc = client.post(f"/api/sessions/{session_id}/recalculate", json={})
    assert recalc.status_code == 200
    payload = recalc.get_json()
    meta = payload["meta"]
    assert "remaining_budget" in meta
    assert "available_count" in meta
    assert "non_zero_working_bid_count" in meta
    assert "shortlist_suggested_ids" in meta
    assert isinstance(meta["shortlist_suggested_ids"], list)
    assert int(meta["non_zero_working_bid_count"]) >= 2
    assert "strategy" in payload
    assert "scenarios" in payload["strategy"]


def test_connection_recommendation_respects_capacity_limits(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Capacity-limited recommendation")
    wind_id = _type_map(client)["wind"]

    with app.app_context():
        session = db.session.get(GameSession, session_id)
        assert session is not None
        rules_cfg = dict(session.ruleset.config_json or {})
        network_cfg = dict(rules_cfg.get("network", {}) or {})
        network_cfg["connection_loss_pct_by_point"] = {"A": 0.0, "B": 5.0}
        network_cfg["connection_capacity_mw_by_point"] = {"A": 1.0, "B": 10.0}
        network_cfg["line_max_power_mw"] = 1.0
        rules_cfg["network"] = network_cfg
        session.ruleset.config_json = rules_cfg
        db.session.add(session.ruleset)

        existing = ObjectInstance(
            session_id=session.id,
            object_type_id=wind_id,
            custom_name="Existing A",
            district="A",
            current_parameters_json={"connection_point": "A", "generation_mw": 0.8},
            is_active=True,
        )
        db.session.add(existing)
        db.session.commit()

    resp = client.get(f"/system/{session_id}/objects/new?object_type_id={wind_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Рекомендация по подключению:" in html
    assert "Остаток лимита:" in html
    assert (
        "Подключение не рекомендовано" in html
        or "Точки, которые не проходят по лимитам" in html
        or "Допустимые альтернативы" in html
    )


def test_invalid_topology_blocks_system_check_inside_lot_evaluation(client, app):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Topology mismatch guard")
    tmap = _type_map(client)

    main = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": tmap["main_substation"],
            "custom_name": "Main",
            "parent_instance_id": None,
        },
    )
    assert main.status_code == 200
    main_id = int(main.get_json()["item"]["id"])

    mini = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": tmap["mini_substation_a"],
            "custom_name": "Mini",
            "parent_instance_id": main_id,
        },
    )
    assert mini.status_code == 200
    mini_id = int(mini.get_json()["item"]["id"])

    with app.app_context():
        row = db.session.get(ObjectInstance, main_id)
        assert row is not None
        row.parent_instance_id = mini_id
        db.session.add(row)
        db.session.commit()

    lot = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Topology lot",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 90,
            "items": [{"object_type_id": tmap["wind"], "quantity": 1}],
        },
    )
    assert lot.status_code == 200
    lot_id = int(lot.get_json()["item"]["id"])
    _upload_and_select_forecast(client, session_id)

    evaluation = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert evaluation.status_code == 200
    payload = evaluation.get_json()["item"]
    assert payload["system_check"]["status"] == "blocked"
    assert payload["system_check"]["topology_invalid"] is True
    assert float(payload["working_bid"]) == pytest.approx(0.0)
    assert float(payload["decision_summary"]["working_bid"]) == pytest.approx(0.0)

    system_html = client.get(f"/system/{session_id}").get_data(as_text=True)
    assert "Обнаружен цикл в дереве сети" in system_html


def test_readme_mentions_buy_guard_quick_auction_and_topology_write_guard():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Покупка лота блокируется, если активный прогноз несовместим" in readme
    assert "Оценка в quick auction не меняет `current_bid`" in readme
    assert "Циклы и разрывы до главной подстанции блокируются на write-path" in readme
