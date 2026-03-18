from __future__ import annotations

import json

from tests.web_helpers import create_session, login


def test_dashboard_uses_session_terms_and_unified_analysis_help(client):
    login(client, "admin", "admin123")

    session_id = create_session(client, title="UX session", selected_strategy="eco")

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Создать сессию" in html
    assert "Как читается оценка" in html
    assert "Открыть сессию" in html
    assert "Единый анализ" in html
    assert f"/sessions/{session_id}" in html


def test_session_can_be_deleted_via_ssr_confirm_page(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Delete me")

    confirm = client.get(f"/sessions/{session_id}/delete")
    assert confirm.status_code == 200
    confirm_html = confirm.get_data(as_text=True)
    assert "Удаление сессии" in confirm_html
    assert "Delete me" in confirm_html

    resp = client.post(f"/sessions/{session_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Сессия «Delete me» удалена" in html
    assert f"/sessions/{session_id}" not in html
    assert "Создать сессию" in html


def test_catalog_renders_glossary_for_russian_users(client):
    login(client, "admin", "admin123")

    resp = client.get("/catalog")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Англо-русский словарь терминов" in html
    assert "generation_mw" in html
    assert "объём генерации" in html
    assert "bundled_forecast" in html
    assert "встроенный прогноз тестовой игры" in html


def test_admin_edit_forms_hide_raw_json_and_show_typed_sections(client):
    login(client, "admin", "admin123")

    object_type_resp = client.get("/admin/object-types/new")
    assert object_type_resp.status_code == 200
    object_type_html = object_type_resp.get_data(as_text=True)
    assert "JSON параметров по умолчанию" not in object_type_html
    assert "JSON редактируемых полей" not in object_type_html
    assert "JSON правил" not in object_type_html
    assert "Разрешить редактирование этого параметра" in object_type_html
    assert "Ограничения и логика" in object_type_html

    ruleset_resp = client.get("/admin/rulesets/new")
    assert ruleset_resp.status_code == 200
    ruleset_html = ruleset_resp.get_data(as_text=True)
    assert "JSON конфигурации" not in ruleset_html
    assert "JSON настроек модели" not in ruleset_html
    assert "Профили стратегий" in ruleset_html
    assert "Сценарии" in ruleset_html
    assert "Рынок" in ruleset_html


def test_system_and_lot_edit_pages_hide_unwanted_analysis_noise(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Noise check")

    system_resp = client.get(f"/system/{session_id}")
    assert system_resp.status_code == 200
    system_html = system_resp.get_data(as_text=True)
    assert "Режим анализа" not in system_html

    lot_edit_resp = client.get(f"/lots/{session_id}/edit")
    assert lot_edit_resp.status_code == 200
    lot_edit_html = lot_edit_resp.get_data(as_text=True)
    assert "raw JSON" not in lot_edit_html
    assert "Состав JSON" not in lot_edit_html


def test_workbench_focuses_on_forecast_portfolio_and_export_actions(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Workbench actions")

    resp = client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Бюджет и портфель" in html
    assert "Активный прогноз" in html
    assert "Пересчитать все лоты" in html
    assert f"/api/sessions/{session_id}/export.json" in html
    assert f"/api/sessions/{session_id}/evaluations.csv" in html
    assert f"/evaluation/{session_id}" not in html
    assert f"/recommend/{session_id}" not in html
    assert "Контур работы" not in html
    assert "Продуктовый контур" not in html


def test_dashboard_imports_session_via_ssr_form(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Export me")
    payload = client.get(f"/api/sessions/{session_id}/export.json").get_json()["item"]

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "/sessions/import" in dashboard.get_data(as_text=True)

    imported = client.post(
        "/sessions/import",
        data={"payload_json": json.dumps(payload)},
        follow_redirects=False,
    )
    assert imported.status_code in (302, 303)
    assert "/sessions/" in imported.headers["Location"]


def test_quick_auction_uses_user_facing_actions_without_debug_block(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Quick UX")
    type_rows = client.get("/api/object-types").get_json()["items"]
    wind_id = next(row["id"] for row in type_rows if row["code"] == "wind")

    lot_a = client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "QA lot 1",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 105,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    ).get_json()["item"]["id"]
    client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "QA lot 2",
            "scope": "normal",
            "base_bid": 90,
            "current_bid": 95,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )

    resp = client.get(f"/quick-auction/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Технический ответ" not in html
    assert "Цена покупки (по умолчанию — рекомендуемая ставка)" in html
    assert 'id="purchasePriceInput"' in html
    assert f"/lots/item/{lot_a}" in html
    assert "Открыть сравнение" not in html
    assert "Пересчитать все лоты" in html


def test_quick_auction_script_keeps_selected_lot_after_refresh(client):
    js_resp = client.get("/static/js/analysis/quick_auction.js")
    assert js_resp.status_code == 200
    js = js_resp.get_data(as_text=True)

    assert "async function refreshRanking(preferredLotId)" in js
    assert "const selectedLotId = Number(preferredLotId ?? $('currentLotId')?.value ?? 0);" in js
    assert "const selected = rankingItemByLotId(selectedLotId) || state.ranking[0] || null;" in js
    assert "await refreshRanking(Number(button.dataset.lotId || 0));" in js
    assert "function isHotkeyTarget(event)" in js
    assert "tag === 'input' || tag === 'textarea' || tag === 'select'" in js
    assert "event.preventDefault();" in js
    assert "async function recalculateAllLots(preferredLotId)" in js
    assert "await apiFetchJson(`/api/lots/${lotId}/buy`" in js
    assert "const refresh = data?.refresh || null;" in js
    assert "await refreshRanking(0);" in js
    assert "function syncPurchasePriceInput" in js
    assert "apiFetchJson(`/api/lots/${lotId}`, {" not in js
    assert "body: JSON.stringify({current_bid: bid})" not in js
    assert "function syncAuctionListWithRanking(rows)" in js
    assert "const fragment = document.createDocumentFragment();" in js
    assert "item = existing || document.createElement('li');" in js
    assert "list.innerHTML = '';" in js
    assert "list.appendChild(fragment);" in js
    assert "state.visibleRanking = filtered;" in js
    assert "const row = state.visibleRanking[index];" in js
    assert "const lotId = Number(row?.lot_id || 0);" in js


def test_layout_contract_for_compact_lots_and_quick_auction_tables(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Layout contract session")
    type_rows = client.get("/api/object-types").get_json()["items"]
    wind_id = next(row["id"] for row in type_rows if row["code"] == "wind")
    client.post(
        "/api/lots",
        json={
            "session_id": session_id,
            "name": "Очень длинное имя лота для проверки компактного рендера в таблице и предотвращения наложения текста",
            "scope": "normal",
            "base_bid": 100,
            "current_bid": 101,
            "items": [{"object_type_id": wind_id, "quantity": 1}],
        },
    )

    lots_html = client.get(f"/lots/{session_id}").get_data(as_text=True)
    assert ('class="lot-bid-reason text-clamp-2"' in lots_html) or ('class="lot-bid-meta"' in lots_html)
    assert 'class="row-actions row-actions-inline"' in lots_html

    quick_html = client.get(f"/quick-auction/{session_id}").get_data(as_text=True)
    assert 'class="col-text qa-name-cell"' in quick_html
    assert 'class="col-text qa-reason-cell"' in quick_html
    assert 'class="qa-row-actions"' in quick_html

    css = client.get("/static/css/tailwind.css").get_data(as_text=True)
    assert "grid-template-columns: 206px minmax(0, 1fr);" in css
    assert ".table-lots,\n.table-auction-ranking" in css
    assert ".qa-row-actions" in css


def test_forecast_page_shows_compatibility_block(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Forecast compatibility UX")

    resp = client.get(f"/forecast/{session_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Совместимость прогноза" in html
    assert "Покрытые типы объектов" in html
    assert "Лишние колонки CSV" in html
    assert "Горизонт" in html
    assert "Периодов" not in html
    assert 'class="forecast-columns-grid mt-4"' in html
    assert 'class="mapping-list mt-2"' in html
    assert "load_housea" not in html
