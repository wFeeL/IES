from __future__ import annotations

import pytest

from ies_bot_skeleton.application.portfolio import buy_lot
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import Forecast, GameSession, Lot, LotItem, ObjectInstance, ObjectType
from ies_bot_skeleton.web.services.evaluation import evaluate_lot
from ies_bot_skeleton.web.services.forecast_service import parse_and_store_forecast


def _forecast_csv() -> bytes:
    rows = ["tick,illumination,wind_main,wind_west,houseA,office,market_price,sell_price,balancing_penalty_price"]
    for tick in range(48):
        rows.append(
            ",".join(
                [
                    str(tick),
                    f"{0.8 if 12 <= tick <= 30 else 0.15:.2f}",
                    f"{8.5 + (tick % 5) * 0.6:.2f}",
                    f"{7.0 + (tick % 7) * 0.5:.2f}",
                    f"{4.0 + (2.0 if tick < 8 or tick > 40 else 0.4):.2f}",
                    f"{2.5 + (3.5 if 10 <= tick <= 30 else 0.2):.2f}",
                    f"{9.0 + (1.2 if tick < 7 or tick > 41 else 0.0):.2f}",
                    f"{8.2 + (1.4 if 13 <= tick <= 28 else 0.0):.2f}",
                    "6.0",
                ]
            )
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_evaluate_lot_returns_2026_delta_profit_payload(app):
    with app.app_context():
        session = GameSession(title="Scoring 2026", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        main = ObjectInstance(session_id=session.id, object_type_id=type_map["main_substation"].id)
        db.session.add(main)
        db.session.flush()
        mini = ObjectInstance(
            session_id=session.id,
            object_type_id=type_map["mini_substation"].id,
            parent_instance_id=main.id,
            district="gen_west",
        )
        db.session.add(mini)
        db.session.flush()

        lot = Lot(
            session_id=session.id,
            name="Solar lot",
            scope="normal",
            status="available",
            base_bid=5.0,
            current_bid=5.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["solar"].id, quantity=1))
        db.session.commit()

        forecast_row, _ = parse_and_store_forecast(
            session_id=session.id,
            name="Scoring 2026",
            source_file="scoring.csv",
            content=_forecast_csv(),
        )
        forecast = db.session.get(Forecast, forecast_row.id)
        assert forecast is not None

        payload = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

        assert "expected_delta_profit" in payload
        assert "break_even_tariff" in payload
        assert "recommended_bid_or_tariff" in payload
        assert "system_check" in payload
        assert "storage_value" in payload["metrics"]
        assert payload["decision_summary"]["bid_formula"] == "unified_lot_optimizer_v2"


def test_evaluate_lot_flags_topology_risk_when_main_substation_missing(app):
    with app.app_context():
        session = GameSession(title="No main", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        lot = Lot(
            session_id=session.id,
            name="Solar lot",
            scope="normal",
            status="available",
            base_bid=5.0,
            current_bid=5.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["solar"].id, quantity=1))
        db.session.commit()

        forecast_row, _ = parse_and_store_forecast(
            session_id=session.id,
            name="Scoring 2026",
            source_file="scoring.csv",
            content=_forecast_csv(),
        )
        forecast = db.session.get(Forecast, forecast_row.id)
        assert forecast is not None

        payload = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

        assert payload["topology_risk"] in {"medium", "high"}
        assert payload["system_check"]["status"] == "blocked"
        assert any(
            "главная подстанция" in message.lower()
            for message in payload["system_check"]["critical_blocking_errors"]
        )


def test_consumer_payload_exposes_floor_logic(app):
    with app.app_context():
        session = GameSession(title="Consumer floor", ruleset_id=1, selected_strategy="consumer")
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        main = ObjectInstance(session_id=session.id, object_type_id=type_map["main_substation"].id)
        db.session.add(main)
        db.session.flush()
        mini = ObjectInstance(
            session_id=session.id,
            object_type_id=type_map["mini_substation"].id,
            parent_instance_id=main.id,
            district="load_north",
        )
        db.session.add(mini)
        db.session.flush()

        lot = Lot(
            session_id=session.id,
            name="House lot",
            scope="local",
            status="available",
            base_bid=6.0,
            current_bid=6.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["house_a"].id, quantity=1))
        db.session.commit()

        forecast_row, _ = parse_and_store_forecast(
            session_id=session.id,
            name="Consumer floor",
            source_file="scoring.csv",
            content=_forecast_csv(),
        )
        forecast = db.session.get(Forecast, forecast_row.id)
        assert forecast is not None

        payload = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)

        assert payload["decision_summary"]["floor_or_ceiling_type"] == "floor"
        assert payload["decision_summary"]["minimum_acceptable_tariff"] > 0.0
        assert (
            payload["decision_summary"]["recommended_walkdown_tariff"]
            >= payload["decision_summary"]["minimum_acceptable_tariff"]
        )


def test_cannot_buy_more_than_one_main_substation(app):
    with app.app_context():
        session = GameSession(title="Single main", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        existing_main = ObjectInstance(session_id=session.id, object_type_id=type_map["main_substation"].id)
        db.session.add(existing_main)
        db.session.flush()

        lot = Lot(
            session_id=session.id,
            name="Second main",
            scope="global",
            status="available",
            base_bid=10.0,
            current_bid=10.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["main_substation"].id, quantity=1))
        db.session.commit()

        with pytest.raises(ValueError, match="Нельзя купить более одной главной подстанции"):
            buy_lot(session, lot, 10.0)
