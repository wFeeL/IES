from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def _h48_csv_legacy_loads() -> bytes:
    rows = ["tick,wind,illumination,load_housea,load_factory,market_price"]
    for tick in range(48):
        rows.append(
            f"{tick},{2 + (tick % 4)},{0.5 + (tick % 5) * 0.08:.2f},{9 + (tick % 3)},{4 + (tick % 4)},{10 + (tick % 3)}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_lots_table_uses_compact_headers_and_actions_menu(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Lots table")
    type_map = _type_map(client)
    type_rows = client.get("/api/object-types").get_json()["items"]
    type_name = {row["code"]: row["name"] for row in type_rows}

    created = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Очень длинное имя лота для проверки tooltip и ellipsis",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 120,
            "items": [
                {"object_type_id": type_map["wind"], "quantity": 1},
                {"object_type_id": type_map["storage"], "quantity": 1},
                {"object_type_id": type_map["office"], "quantity": 1},
                {"object_type_id": type_map["house"], "quantity": 1},
                {"object_type_id": type_map["factory"], "quantity": 1},
            ],
        },
    )
    assert created.status_code == 200

    resp = client.get(f"/lots/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Stage A · Аукцион 2026" in html
    assert "Консолидированная оценка лотов" in html
    assert "Optimal purchase price" in html
    assert "Hard limit" in html
    assert "Risk-adjusted" in html
    assert 'class="grid cols-4 gap-3 mt-3 lots-filter-grid"' in html
    assert "Открыть анализ" in html
    assert "Очень длинное имя лота для проверки tooltip и ellipsis" in html
    assert "profit after bid" not in html
    assert "budget left" not in html
    assert type_name["wind"] in html
    assert type_name["storage"] in html
    assert type_name["office"] in html
    assert type_name["house"] in html
    assert type_name["factory"] in html


def test_session_dashboard_shows_forecast_portfolio_and_quick_actions(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Dashboard focus")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Сессия → Прогноз → Лоты → Покупка" not in html
    assert "Контур работы" not in html
    assert "Активный прогноз" in html
    assert "Горизонт" in html
    assert "Периодов" not in html
    assert "Готовность данных" in html
    assert "Бюджет и портфель" in html
    assert "Режим" in html
    assert "Каталог комбинаций" in html
    assert "Best singles, pairs, groups и сценарии второго круга" in html
    assert "Совместимость" in html
    assert "Открыть лоты" in html
    assert "Открыть прогноз" in html
    assert "Быстрый аукцион" in html
    assert "Проверить энергосистему" in html
    assert "Экспорт сессии" in html
    assert 'id="strategySnapshotCard"' in html
    assert f'data-strategy-url="/api/sessions/{session_id}/strategy"' in html


def test_theme_toggle_and_css_tokens_present(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Theme smoke")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="themeToggle"' in html
    assert 'const KEY = "ies-theme";' in html
    assert 'document.documentElement.setAttribute("data-theme", normalize(localStorage.getItem(KEY)) || "light");' in html

    css_resp = client.get("/static/css/tailwind.css")
    assert css_resp.status_code == 200
    css = css_resp.get_data(as_text=True)
    assert 'html[data-theme="dark"]' in css
    assert "overflow-wrap: break-word" in css
    assert ".row-actions-menu" in css
    assert ".table-lots.table-lots-compact col.lot-bid-col {" in css

    js_resp = client.get("/static/js/theme.js")
    assert js_resp.status_code == 200
    js = js_resp.get_data(as_text=True)
    assert 'const initialTheme = saved || "light";' in js
    assert 'document.documentElement.setAttribute("data-theme", theme);' in js
    assert "prefers-color-scheme" not in js


def test_lot_detail_renders_non_zero_income_for_legacy_load_forecast(client, app):
    login(client, "admin", "admin123")

    from ies_bot_skeleton.web.extensions import db
    from ies_bot_skeleton.web.models import GameSession, Lot, LotItem, ObjectInstance, ObjectType
    from ies_bot_skeleton.web.services.forecast_service import parse_and_store_forecast

    with app.app_context():
        session = GameSession(title="Legacy load SSR", ruleset_id=1, selected_strategy="balanced")
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        main = ObjectInstance(session_id=session.id, object_type_id=type_map["main_substation"].id)
        db.session.add(main)
        db.session.flush()
        mini = ObjectInstance(
            session_id=session.id,
            object_type_id=type_map["mini_substation_a"].id,
            parent_instance_id=main.id,
        )
        db.session.add(mini)
        db.session.flush()
        db.session.add(
            ObjectInstance(
                session_id=session.id,
                object_type_id=type_map["house"].id,
                parent_instance_id=mini.id,
            )
        )

        lot = Lot(
            session_id=session.id,
            name="Потребительский лот",
            scope="normal",
            status="available",
            base_bid=10.0,
            current_bid=10.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["house"].id, quantity=1))

        forecast, _ = parse_and_store_forecast(
            session_id=session.id,
            name="Legacy load forecast",
            source_file="legacy.csv",
            content=_h48_csv_legacy_loads(),
        )
        session.selected_forecast_id = int(forecast.id)
        db.session.add(session)
        db.session.commit()
        session_id = int(session.id)
        lot_id = int(lot.id)

    evaluation = client.post(f"/api/lots/{lot_id}/evaluate", json={})
    assert evaluation.status_code == 200
    payload = evaluation.get_json()["item"]
    income_total = float(payload["financial_breakdown"]["income"]["total"])
    assert income_total > 0.0

    detail = client.get(f"/lots/item/{lot_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Stage A" in html
    assert "Разложение экономики" in html
    assert "Лучшие пары с этим лотом" in html
    assert "Optimal purchase price" in html
    assert "Рабочий потолок" in html
    assert "Чистая прибыль после целевой ставки" in html
    assert "Остаток бюджета после целевой ставки" in html
    assert 'id="lotPairsCard"' in html
    assert f'data-strategy-url="/api/sessions/{session_id}/strategy?top_n=20"' in html


def test_lot_detail_with_tps_is_not_blocked_by_forecast_coverage(client, app):
    login(client, "admin", "admin123")

    from ies_bot_skeleton.web.extensions import db
    from ies_bot_skeleton.web.models import GameSession, Lot, LotItem, ObjectType
    from ies_bot_skeleton.web.services.forecast_service import parse_and_store_forecast

    with app.app_context():
        session = GameSession(
            title="Blocked lot detail",
            ruleset_id=1,
            selected_strategy="balanced",
        )
        db.session.add(session)
        db.session.flush()

        type_map = {row.code: row for row in db.session.query(ObjectType).all()}
        lot = Lot(
            session_id=session.id,
            name="ТЭС лот",
            scope="normal",
            status="available",
            base_bid=40.0,
            current_bid=40.0,
        )
        db.session.add(lot)
        db.session.flush()
        db.session.add(LotItem(lot_id=lot.id, object_type_id=type_map["tps"].id, quantity=1))

        forecast, _ = parse_and_store_forecast(
            session_id=session.id,
            name="House only",
            source_file="house.csv",
            content=_h48_csv_legacy_loads(),
        )
        session.selected_forecast_id = int(forecast.id)
        db.session.add(session)
        db.session.commit()
        lot_id = int(lot.id)

    resp = client.get(f"/lots/item/{lot_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Оценка лота заблокирована" not in html
    assert "Добавьте в активный прогноз покрытие для типов объектов: tps." not in html
    assert "Либо удалите из этого лота объекты типов: tps." not in html
