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
    ObjectType,
)
from ies_bot_skeleton.web.services.evaluation import (
    _consumer_demand_mw,
    _financial_breakdown,
    _solar_generation_mw,
    _scenario_row,
    _wind_generation_mw,
    evaluate_lot,
)
from ies_bot_skeleton.web.services.forecast_service import (
    build_forecast_pack,
    parse_and_store_forecast,
    summarize_forecast,
)
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
    budget_limited = float(out["budget_limited_bid"])

    assert hard >= 0.0
    assert soft >= 0.0
    assert ceiling >= hard >= soft
    assert soft <= hard + 1e-9
    assert budget_limited == pytest.approx(min(hard, remaining))
    assert out["decision_summary"]["budget_limited_bid"] == pytest.approx(budget_limited)
    assert out["metrics"]["bids"]["budget_remaining"] == pytest.approx(remaining)
    valuation = dict((out["metrics"]["bids"] or {}).get("valuation_model") or {})
    assert valuation["model"] == "valuation_model_v2"
    assert valuation["profile"] == "balanced"
    assert valuation["risk_band"] in {"low", "medium", "high"}
    assert valuation["target_bid"] == pytest.approx(hard)
    assert valuation["budget_limited_bid"] == pytest.approx(budget_limited)
    assert "risk_ratio" in valuation
    assert "v1" in valuation
    assert "v2" in valuation
    ui_rows = list((out["financial_breakdown"] or {}).get("ui_rows") or [])
    assert any(str(row.get("key")) == "entry_price" for row in ui_rows)
    assert any(str(row.get("key")) == "net_profit" for row in ui_rows)
    for row in ui_rows:
        if str(row.get("key")) in {"entry_price", "net_profit"}:
            continue
        assert abs(float(row.get("value", 0.0))) > 1e-6
    assert 0.0 <= float(out["confidence"]) <= 1.0
    assert "delta_score" in out["metrics"]
    assert out["forecast_context"]["source"] == "selected_forecast"
    if hard > remaining:
        assert budget_limited == pytest.approx(remaining)


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
    assert float(out["budget_limited_bid"]) == pytest.approx(0.0)


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

    assert float(losses["overload_penalties"]) == pytest.approx(0.0)
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

    breakdown = _financial_breakdown(base_delta=Delta(), current_price=50.0, hard_bid=20.0)
    assert breakdown["income"]["market_income"] == pytest.approx(0.0)
    assert breakdown["expenses"]["market_purchase"] == pytest.approx(40.0)

    Delta.delta_market_net = -30.0
    breakdown_sell = _financial_breakdown(base_delta=Delta(), current_price=50.0, hard_bid=20.0)
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
