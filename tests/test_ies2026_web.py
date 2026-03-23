from __future__ import annotations

from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.models import GameSession, Lot, LotItem, ObjectType

from tests.web_helpers import create_session, login


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
