from __future__ import annotations

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import (
    Forecast,
    ForecastPeriod,
    GameSession,
    Lot,
    LotItem,
    ObjectInstance,
    ObjectType,
)
from ies_bot_skeleton.web.services.evaluation import (
    _consumer_demand_mw,
    _financial_breakdown,
    _solar_generation_mw,
    _scenario_row,
    _valuation_model_v3,
    _wind_generation_mw,
    evaluate_lot,
)
from ies_bot_skeleton.web.services.forecast_service import (
    build_forecast_pack,
    parse_and_store_forecast,
    summarize_forecast,
)
from ies_bot_skeleton.web.services.adapter import session_to_state
from ies_bot_skeleton.web.services.seed import ensure_seed_data


def _h48_csv_house_market() -> bytes:
    rows = ["tick,wind,illumination,houseA,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{3 + (tick % 5)},{0.4 + (tick % 6) * 0.1:.2f},{10 + (tick % 4)},{11 + (tick % 3)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _h48_csv_legacy_loads() -> bytes:
    rows = ["tick,wind,illumination,load_housea,load_factory,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{2 + (tick % 4)},{0.5 + (tick % 5) * 0.08:.2f},{9 + (tick % 3)},{4 + (tick % 4)},{10 + (tick % 3)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


@pytest.fixture()
def app_ctx():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")

        session = GameSession(title="Scoring", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        from ies_bot_skeleton.web.models import ObjectInstance

        tmap = {x.code: x for x in db.session.query(ObjectType).all()}
        main = ObjectInstance(session_id=session.id, object_type_id=tmap["main_substation"].id)
        db.session.add(main)
        db.session.flush()
        mini = ObjectInstance(
            session_id=session.id,
            object_type_id=tmap["mini_substation_a"].id,
            parent_instance_id=main.id,
        )
        db.session.add(mini)
        db.session.flush()
        db.session.add(
            ObjectInstance(
                session_id=session.id,
                object_type_id=tmap["house"].id,
                parent_instance_id=mini.id,
            )
        )

        lot = Lot(session_id=session.id, name="Scoring Lot", scope="normal", status="available")
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["wind"].id, quantity=1))
        db.session.commit()

        csv_payload = _h48_csv_house_market()
        forecast, _ = parse_and_store_forecast(
            session_id=session.id,
            name="S",
            source_file="s.csv",
            content=csv_payload,
        )

        yield {
            "app": app,
            "session": db.session.get(GameSession, session.id),
            "lot": db.session.get(Lot, lot.id),
            "forecast": db.session.get(Forecast, forecast.id),
        }

        db.session.remove()
        db.drop_all()


def test_recommended_bid_is_within_budget(app_ctx):
    session = app_ctx["session"]
    forecast = app_ctx["forecast"]
    tmap = {x.code: x for x in db.session.query(ObjectType).all()}

    lot = Lot(
        session_id=session.id,
        name="High value generator",
        scope="normal",
        status="available",
        base_bid=10.0,
        current_bid=10.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["wind"].id, quantity=8))
    session.budget_total = 15.0
    db.session.add(session)
    db.session.commit()

    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

    hard = float(out["recommended_bid_hard"])
    soft = float(out["recommended_bid_soft"])
    ceiling = float(out["hard_ceiling_bid"])
    remaining = float(out["portfolio_context"]["remaining_budget"])
    assert float(out["portfolio_context"]["start_budget"]) == pytest.approx(float(session.budget_total))
    budget_adjusted = float(out["budget_adjusted_bid"])
    decision_summary = dict(out["decision_summary"])
    result = dict((out["financial_breakdown"] or {}).get("result") or {})
    bid_share = float(decision_summary["bid_share"])
    gross_profit_before_bid = float(decision_summary["gross_expected_profit_before_bid"])

    assert hard >= 0.0
    assert soft >= 0.0
    assert ceiling >= hard >= soft
    assert soft <= hard + 1e-9
    assert budget_adjusted == pytest.approx(min(hard, remaining))
    assert out["decision_summary"]["budget_adjusted_bid"] == pytest.approx(budget_adjusted)
    assert out["metrics"]["bids"]["budget_remaining"] == pytest.approx(remaining)
    valuation = dict((out["metrics"]["bids"] or {}).get("valuation_model") or {})
    assert valuation["model"] == "valuation_model_v3"
    assert valuation["profile"] == "generator"
    assert valuation["risk_band"] in {"low", "medium", "high"}
    assert bid_share == pytest.approx({"low": 0.25, "medium": 0.20, "high": 0.15}[valuation["risk_band"]])
    assert gross_profit_before_bid == pytest.approx(
        float(out["metrics"]["weighted_expected"]) + float(out["decision_factors"]["entry_price"])
    )
    assert valuation["target_bid"] == pytest.approx(hard)
    assert valuation["budget_adjusted_bid"] == pytest.approx(budget_adjusted)
    assert "risk_ratio" in valuation
    assert "role_multipliers" in valuation
    assert "portfolio_synergy" in valuation
    assert "system_fit_score" in valuation
    assert out["recommended_bid"] == pytest.approx(out["working_bid"])
    assert out["working_bid"] == pytest.approx(budget_adjusted)
    assert out["recommended_bid"] <= remaining + 1e-9
    assert decision_summary["net_profit_at_recommended_bid"] == pytest.approx(
        gross_profit_before_bid - float(out["recommended_bid"])
    )
    assert decision_summary["net_profit_at_max_bid"] == pytest.approx(
        gross_profit_before_bid - float(out["max_bid"])
    )
    assert decision_summary["remaining_budget_after_recommended_bid"] == pytest.approx(
        remaining - float(out["recommended_bid"])
    )
    assert decision_summary["remaining_budget_after_recommended_bid"] >= 0.0
    assert decision_summary["net_profit_at_max_bid"] > 0.0
    assert result["gross_profit_before_bid"] == pytest.approx(
        result["net_profit_at_current_price"] + float(out["decision_factors"]["entry_price"])
    )
    assert result["remaining_budget_after_recommended_bid"] == pytest.approx(
        remaining - float(out["recommended_bid"])
    )
    assert decision_summary["budget_preservation_note"]
    ui_rows = list((out["financial_breakdown"] or {}).get("ui_rows") or [])
    assert any(str(row.get("key")) == "entry_price" for row in ui_rows)
    assert any(str(row.get("key")) == "net_profit" for row in ui_rows)
    for row in ui_rows:
        if str(row.get("key")) in {
            "entry_price",
            "net_profit",
            "remaining_budget_after_recommended_bid",
            "remaining_budget_after_max_bid",
        }:
            continue
        assert abs(float(row.get("value", 0.0))) > 1e-6
    assert 0.0 <= float(out["confidence"]) <= 1.0
    assert "delta_score" in out["metrics"]
    assert out["forecast_context"]["source"] == "selected_forecast"
    assert "system_check" in out
    assert "message" in out["system_check"]
    if hard > remaining:
        assert budget_adjusted == pytest.approx(remaining)


def test_valuation_model_keeps_non_zero_working_bid_for_slim_positive_expected_value():
    out = _valuation_model_v3(
        p_worst=-5.0,
        p_base=30.0,
        p_best=55.0,
        p_exp=4.0,
        entry_price_total=6.0,
        horizon_ticks=48,
        remaining_budget=50.0,
        evaluation_cfg={},
        role_profile={
            "dominant_role": "generator",
            "multipliers": {"target": 1.08, "cautious": 1.0, "ceiling": 1.06},
        },
        portfolio_synergy=0.0,
        system_fit_score=0.0,
    )

    assert float(out["target_bid"]) == pytest.approx((4.0 + 6.0) * 0.15)
    assert float(out["budget_adjusted_bid"]) == pytest.approx(float(out["target_bid"]))
    assert float(out["working_bid"]) == pytest.approx(float(out["budget_adjusted_bid"]))
    assert float(out["remaining_budget_after_recommended_bid"]) == pytest.approx(
        50.0 - float(out["working_bid"])
    )


def test_valuation_model_zeroes_bids_when_weighted_expected_is_negative():
    out = _valuation_model_v3(
        p_worst=-2826.1933439999993,
        p_base=211.1122560000003,
        p_best=902.5618560000001,
        p_exp=-748.2272639999994,
        entry_price_total=120.0,
        horizon_ticks=48,
        remaining_budget=120.0,
        evaluation_cfg={},
        role_profile={
            "dominant_role": "mixed",
            "multipliers": {"target": 1.0, "cautious": 1.0, "ceiling": 1.0},
        },
        portfolio_synergy=1888.8691200000005,
        system_fit_score=-12.0,
    )

    assert float(out["target_bid"]) == pytest.approx(0.0)
    assert float(out["budget_adjusted_bid"]) == pytest.approx(0.0)
    assert float(out["working_bid"]) == pytest.approx(0.0)
    assert float(out["remaining_budget_after_recommended_bid"]) == pytest.approx(120.0)


def test_legacy_load_columns_are_mapped_to_canonical_series(app_ctx):
    session = app_ctx["session"]
    type_house = db.session.query(ObjectType).filter_by(code="house").one()

    lot = Lot(
        session_id=session.id,
        name="Legacy load lot",
        scope="normal",
        status="available",
        base_bid=10.0,
        current_bid=10.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=type_house.id, quantity=1))
    db.session.commit()

    legacy_csv = _h48_csv_legacy_loads()
    legacy_forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="Legacy load format",
        source_file="legacy.csv",
        content=legacy_csv,
    )

    out = evaluate_lot(session=session, lot=lot, forecast=legacy_forecast, persist=False)

    assert float(out["financial_breakdown"]["income"]["total"]) > 0.0
    assert float(out["metrics"]["scenario_delta"]["base"]["delta_income"]) > 0.0


def test_houseb_alias_forecast_profile_is_supported(app_ctx):
    session = app_ctx["session"]
    type_house = db.session.query(ObjectType).filter_by(code="house").one()

    lot = Lot(
        session_id=session.id,
        name="HouseB profile lot",
        scope="normal",
        status="available",
        base_bid=10.0,
        current_bid=10.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=type_house.id, quantity=1))
    db.session.commit()

    rows = ["tick,wind,illumination,houseB,market_price"]
    for tick in range(48):
        rows.append(f"{tick},3,0.6,{8 + (tick % 3)},10")
    houseb_csv = ("\n".join(rows) + "\n").encode("utf-8")
    houseb_forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="HouseB alias",
        source_file="houseb.csv",
        content=houseb_csv,
    )

    summary = summarize_forecast(houseb_forecast)
    load_display = list(summary.get("load_series_display") or [])
    assert load_display
    assert any(str(row.get("label")) == "houseB" for row in load_display)
    assert all(str(row.get("key")) != "class3" for row in load_display)
    assert "houseB" in list(summary.get("mapped_raw_columns") or [])

    out = evaluate_lot(session=session, lot=lot, forecast=houseb_forecast, persist=False)
    assert float(out["financial_breakdown"]["income"]["served_load_revenue"]) > 0.0


def test_forecast_display_rows_use_human_labels_and_service_group(app_ctx):
    session = app_ctx["session"]
    forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="Display labels",
        source_file="display.csv",
        content=_h48_csv_legacy_loads(),
    )

    summary = summarize_forecast(forecast)
    display_rows = list(summary.get("load_series_display") or [])
    labels = {str(row.get("label")) for row in display_rows}
    assert "load_housea" in labels
    assert "load_factory" in labels
    assert "class3" not in labels

    stats_display = list(summary.get("mapped_raw_stats_display") or [])
    stat_labels = {str(row.get("label")) for row in stats_display}
    assert "load_housea" in stat_labels
    assert "load_factory" in stat_labels
    assert "class3" not in stat_labels


def test_forecast_value_interpretation_uses_absolute_branches_for_large_values():
    # Consumer: >1.5 means absolute demand in MW, not multiplier.
    assert _consumer_demand_mw(
        expected_consumption_mw=5.0, profile_value=1.2, load_scale=1.0
    ) == pytest.approx(6.0)
    assert _consumer_demand_mw(
        expected_consumption_mw=5.0, profile_value=8.0, load_scale=1.0
    ) == pytest.approx(8.0)

    # Generation: large values are capped by installed generation in absolute branch.
    wind_mw = _wind_generation_mw(
        wind_value=6.0,
        generation_mw=4.0,
        efficiency=1.0,
        object_defaults={"wind_k_default": 0.1},
    )
    solar_mw = _solar_generation_mw(solar_value=7.0, generation_mw=3.0, efficiency=1.0)
    assert wind_mw <= 4.0 + 1e-9
    assert solar_mw <= 3.0 + 1e-9


def test_generator_income_does_not_double_count_internal_supply(app_ctx):
    session = app_ctx["session"]
    lot = app_ctx["lot"]
    forecast = app_ctx["forecast"]

    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)
    income = out["financial_breakdown"]["income"]
    decomposition = out["financial_breakdown"]["decomposition"]

    assert income["total"] == pytest.approx(
        income["object_income"] + income["market_income"] + income["eco_value"]
    )
    assert decomposition["avoided_market_purchase_value"] >= 0.0
    assert decomposition["export_revenue"] == pytest.approx(income["market_income"])


def test_storage_without_real_dispatch_has_no_artificial_positive_value(app_ctx):
    session = app_ctx["session"]
    forecast = app_ctx["forecast"]
    tmap = {x.code: x for x in db.session.query(ObjectType).all()}

    lot = Lot(
        session_id=session.id,
        name="Idle storage",
        scope="normal",
        status="available",
        base_bid=30.0,
        current_bid=30.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["storage"].id, quantity=1))
    db.session.commit()

    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

    assert float(out["metrics"]["role_breakdown"]["storage"]) == pytest.approx(0.0)
    assert float(out["target_bid"]) == pytest.approx(0.0)
    assert float(out["budget_adjusted_bid"]) == pytest.approx(0.0)


def test_test_game_rules_keep_overload_zero_but_produce_nonzero_risk_signal(app_ctx):
    session = app_ctx["session"]
    forecast = app_ctx["forecast"]
    tmap = {x.code: x for x in db.session.query(ObjectType).all()}

    lot = Lot(
        session_id=session.id,
        name="Wide wind lot",
        scope="normal",
        status="available",
        base_bid=20.0,
        current_bid=20.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["wind"].id, quantity=8))
    db.session.commit()

    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)
    losses = out["financial_breakdown"]["losses_and_risks"]

    assert float(losses["overload_penalties"]) > 0.0
    assert float(losses["risk_total"]) > 0.0


def test_infrastructure_without_constraint_has_no_artificial_positive_value(app_ctx):
    session = app_ctx["session"]
    forecast = app_ctx["forecast"]
    tmap = {x.code: x for x in db.session.query(ObjectType).all()}

    lot = Lot(
        session_id=session.id,
        name="Idle infra",
        scope="normal",
        status="available",
        base_bid=15.0,
        current_bid=15.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["mini_substation_b"].id, quantity=1))
    db.session.commit()

    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

    assert float(out["metrics"]["role_breakdown"]["infrastructure"]) == pytest.approx(0.0)
    assert float(out["target_bid"]) == pytest.approx(0.0)


def test_consumer_bid_degrades_when_supply_is_not_covered(app_ctx):
    session = app_ctx["session"]
    tmap = {x.code: x for x in db.session.query(ObjectType).all()}

    session.ruleset.config_json = {
        **dict(session.ruleset.config_json or {}),
        "market": {
            **dict((session.ruleset.config_json or {}).get("market", {}) or {}),
            "market_max_power": 1.0,
        },
    }
    db.session.add(session.ruleset)

    lot = Lot(
        session_id=session.id,
        name="Heavy consumer",
        scope="normal",
        status="available",
        base_bid=25.0,
        current_bid=25.0,
    )
    db.session.add(lot)
    db.session.flush()
    db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["factory"].id, quantity=4))
    limited_forecast, _ = parse_and_store_forecast(
        session_id=session.id,
        name="Limited factory forecast",
        source_file="limited_factory.csv",
        content=_h48_csv_legacy_loads(),
    )
    db.session.commit()

    out = evaluate_lot(session=session, lot=lot, forecast=limited_forecast, persist=False)

    assert float(out["financial_breakdown"]["losses_and_risks"]["deficit_penalties"]) > 0.0
    assert float(out["target_bid"]) == pytest.approx(0.0)


def test_build_forecast_pack_supports_legacy_load_keys_from_db(app_ctx):
    session = app_ctx["session"]
    forecast = Forecast(
        session_id=session.id,
        name="Legacy DB forecast",
        source_file="db.json",
        column_map_json={},
        metadata_json={},
    )
    forecast.periods = [
        ForecastPeriod(
            tick=1,
            illumination=0.4,
            wind=2.0,
            market_price=10.0,
            consumption_json={"load_housea": 4.0, "load_office": 2.0},
            extra_json={},
        ),
        ForecastPeriod(
            tick=2,
            illumination=0.6,
            wind=3.0,
            market_price=11.0,
            consumption_json={"load_housea": 6.0, "consumption_office": 3.0},
            extra_json={},
        ),
    ]
    db.session.add(forecast)
    db.session.commit()

    pack = build_forecast_pack(forecast)
    summary = summarize_forecast(forecast)

    assert pack["load"]["housea"][1] == pytest.approx(4.0)
    assert pack["load"]["office"][2] == pytest.approx(3.0)
    assert pack["load"]["class3"][1] == pytest.approx(6.0)
    assert pack["load"]["load_housea"][1] == pytest.approx(4.0)
    assert "housea" in summary["internal_canonical_load_series"]
    assert "office" in summary["internal_canonical_load_series"]
    assert summary["mapped_raw_columns"] == []
    assert summary["consumer_averages"]["housea"] == pytest.approx(5.0)


def test_financial_breakdown_uses_correct_market_sign():
    class Delta:
        delta_income = 100.0
        delta_market_net = 40.0
        delta_eco_value = 5.0
        delta_contracts = 10.0
        delta_fuel_and_taxes = 7.0
        delta_network_losses_cost = 2.0
        delta_penalties = 1.0
        delta_risk_penalty = 3.0
        delta_total = 42.0
        flags = []

    breakdown = _financial_breakdown(
        base_delta=Delta(),
        current_price=50.0,
        hard_bid=20.0,
        recommended_bid=12.0,
        max_bid=18.0,
        remaining_budget=40.0,
    )
    assert breakdown["income"]["market_income"] == pytest.approx(0.0)
    assert breakdown["expenses"]["market_purchase"] == pytest.approx(40.0)
    assert breakdown["result"]["net_profit_at_recommended_bid"] == pytest.approx(
        breakdown["result"]["gross_profit_before_bid"] - 12.0
    )
    assert breakdown["result"]["remaining_budget_after_recommended_bid"] == pytest.approx(28.0)

    Delta.delta_market_net = -30.0
    breakdown_sell = _financial_breakdown(
        base_delta=Delta(),
        current_price=50.0,
        hard_bid=20.0,
        recommended_bid=12.0,
        max_bid=18.0,
        remaining_budget=40.0,
    )
    assert breakdown_sell["income"]["market_income"] == pytest.approx(30.0)
    assert breakdown_sell["expenses"]["market_purchase"] == pytest.approx(0.0)


def test_scenario_row_uses_correct_market_sign():
    class Delta:
        delta_income = 50.0
        delta_market_net = 25.0
        delta_eco_value = 0.0
        delta_contracts = 5.0
        delta_fuel_and_taxes = 5.0
        delta_penalties = 0.0
        delta_network_losses_cost = 0.0
        delta_risk_penalty = 0.0
        delta_total = 10.0

    row = _scenario_row(
        label="Base",
        delta_obj=Delta(),
        current_price=20.0,
        pwin=0.35,
        remaining_budget=100.0,
    )
    assert row["income_total"] == pytest.approx(50.0)
    assert row["expenses_total"] == pytest.approx(55.0)
    assert row["gross_profit_before_bid"] == pytest.approx(max(0.0, row["net_profit"] + 20.0))

    Delta.delta_market_net = -12.0
    row_sell = _scenario_row(
        label="Base",
        delta_obj=Delta(),
        current_price=20.0,
        pwin=0.35,
        remaining_budget=100.0,
    )
    assert row_sell["income_total"] == pytest.approx(62.0)
    assert row_sell["expenses_total"] == pytest.approx(30.0)


def test_session_budget_counts_allpay_spend(app_ctx):
    session = app_ctx["session"]
    session.budget_total = 200.0
    session.allpay_spent = 37.5
    db.session.add(session)
    db.session.commit()

    payload = session.to_dict()
    assert payload["start_budget"] == pytest.approx(200.0)
    assert payload["purchase_spent"] == pytest.approx(0.0)
    assert payload["allpay_spent"] == pytest.approx(37.5)
    assert payload["spent_total"] == pytest.approx(37.5)
    assert payload["remaining_budget"] == pytest.approx(162.5)


def test_adapter_budget_mapping_keeps_purchase_and_allpay_separate(app_ctx):
    session = app_ctx["session"]
    lot = app_ctx["lot"]

    session.budget_total = 300.0
    session.allpay_spent = 27.5
    lot.status = "bought"
    lot.purchase_price = 61.0
    pending = ObjectInstance(
        session_id=session.id,
        object_type_id=lot.items[0].object_type_id,
        custom_name="Pending from bought lot",
        current_parameters_json={"generation_mw": 2.0},
        source_lot_id=lot.id,
        parent_instance_id=None,
        district="default",
        is_active=True,
    )
    db.session.add(session)
    db.session.add(lot)
    db.session.add(pending)
    db.session.commit()

    cfg = dict((session.ruleset.config_json or {}) if session.ruleset is not None else {})
    state, owned_items = session_to_state(session, cfg)
    session_payload = session.to_dict()

    assert state.budget.cash == pytest.approx(300.0)
    assert state.budget.allpay_spent == pytest.approx(27.5)
    assert state.budget.allpay_spent != pytest.approx(61.0)
    assert state.budget.allpay_spent != pytest.approx(88.5)
    assert f"LOT{int(lot.id)}" in list(state.owned_lots or [])
    assert session_payload["purchase_spent"] == pytest.approx(61.0)
    assert session_payload["allpay_spent"] == pytest.approx(27.5)
    assert session_payload["spent_total"] == pytest.approx(88.5)
    assert all(str(item.meta.get("source_lot_id") or "") != str(lot.id) for item in owned_items)


def test_strategy_fit_returns_unified_single_row(app_ctx):
    from ies_bot_skeleton.web.services.evaluation import strategy_fit

    out = strategy_fit(
        session=app_ctx["session"],
        lot=app_ctx["lot"],
        forecast=app_ctx["forecast"],
    )

    assert out["analysis_mode"] == "unified"
    assert out["best_strategy"] == "unified"
    assert len(out["rows"]) == 1
    assert out["rows"][0]["strategy"] == "unified"
    assert "decision_factors" in out["rows"][0]
