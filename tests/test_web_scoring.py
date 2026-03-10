from __future__ import annotations

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import Forecast, GameSession, Lot
from ies_bot_skeleton.web.services.evaluation import evaluate_lot
from ies_bot_skeleton.web.services.forecast_service import parse_and_store_forecast
from ies_bot_skeleton.web.services.seed import ensure_seed_data


@pytest.fixture()
def app_ctx():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")

        session = GameSession(title="Scoring", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        # Add start pack so network is minimally valid.
        from ies_bot_skeleton.web.models import ObjectInstance, ObjectType

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

        from ies_bot_skeleton.web.models import LotItem

        db.session.add(LotItem(lot_id=lot.id, object_type_id=tmap["wind"].id, quantity=1))
        db.session.commit()

        csv_payload = b"tick,wind,illumination,houseA,market_price\n0,3,0.6,10,12\n1,5,0.9,11,11\n"
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
    lot = app_ctx["lot"]
    forecast = app_ctx["forecast"]

    out = evaluate_lot(session=session, lot=lot, mode="forecast", forecast=forecast, persist=False)

    hard = float(out["recommended_bid_hard"])
    soft = float(out["recommended_bid_soft"])
    remaining = float(session.budget_total - session.allpay_spent)

    assert hard >= 0.0
    assert soft >= 0.0
    assert hard <= remaining + 1e-9
    assert soft <= hard + 1e-9
    assert 0.0 <= float(out["confidence"]) <= 1.0
    assert "delta_score" in out["metrics"]
