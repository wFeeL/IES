# Data Schemas

## Session export / import JSON

Экспорт сессии из web-приложения возвращает JSON, пригодный для повторного импорта и пост-аукционного анализа.

```json
{
  "schema_version": 5,
  "session": {
    "title": "Тестовая игра",
    "selected_strategy": "balanced",
    "selected_forecast_id": 3,
    "budget_total": 5000.0,
    "allpay_spent": 120.0
  },
  "objects": [],
  "lots": [],
  "forecasts": [],
  "evaluations": []
}
```

Ключевые поля:
- `schema_version` поддерживает `4` и `5`, актуальная схема `5`.
- `session.selected_strategy` хранит активный стратегический профиль SSR/UI и strategy snapshot.
- `session.selected_forecast_id` задаёт активный прогноз сессии.
- `session.allpay_spent` отражает только фактически израсходованный special-case All-Pay бюджет.
- `budget_total` и `allpay_spent` участвуют в общей budget model сессии.
- legacy-поля `analysis_mode` и старые corridor settings при импорте игнорируются ради совместимости.

## Test game preset

Тестовая игра ИЭС 2026 использует data-driven preset:
- стартовый пакет по ruleset `ies_test_game_2026`;
- встроенный прогноз `Прогноз игры`;
- `25` детерминированно-случайных лотов (`20` global, `5` local);
- канонические object codes 2026 в лотах и объектах;
- SSR-стратегии `balanced`, `generation`, `consumer`, `storage`, `eco`, `risk_averse`, `aggressive`.

## Lot payload

Лот хранится как SQLAlchemy-модель и сериализуется через web API:

```json
{
  "id": 12,
  "session_id": 5,
  "name": "G08 · Ветровой запад",
  "scope": "global",
  "status": "available",
  "base_bid": 42.0,
  "current_bid": 42.0,
  "purchase_price": null,
  "available_round": 1,
  "note": "Scope=global, bundle valuation и topology planning обязательны.",
  "items": [
    {
      "object_type_id": 4,
      "object_type_code": "wind",
      "quantity": 2,
      "overrides": {
        "district": "gen_wind_west",
        "wind_channel": "wind_west"
      }
    }
  ]
}
```

Ключевые поля лота:
- `scope`: `start`, `local`, `global` или legacy-совместимый alias.
- `status`: `available`, `bought`, `rejected`.
- `available_round`: номер круга аукциона.
- `items[].overrides` всегда идут через raw-to-canonical mapping и не должны подменять канонический тип объекта.

## Evaluation payload

Оценка лота строится вокруг `expected_delta_profit` и floor/ceiling-логики 2026.

```json
{
  "lot_id": 12,
  "lot_name": "G08 · Ветровой запад",
  "lot_profile": "generator",
  "summary_score": 84.2,
  "strategy_score": 88.5,
  "expected_delta_profit": 126.3,
  "direct_delta_profit": 111.0,
  "enabler_value": 12.8,
  "bundle_synergy_value": 2.5,
  "break_even_tariff": 7.4,
  "recommended_bid_or_tariff": 6.1,
  "forecast_context": {
    "source": "selected_forecast",
    "forecast_id": 3,
    "forecast_name": "Прогноз игры",
    "periods_count": 48
  },
  "decision_summary": {
    "lot_profile": "generator",
    "floor_or_ceiling_type": "ceiling",
    "maximum_acceptable_service_tariff": 7.4,
    "recommended_bid_ceiling": 6.1,
    "soft_ceiling": 5.6,
    "hard_ceiling": 7.4,
    "recommended_opening_bid": 6.1,
    "recommended_counter_bid": 6.2,
    "hard_limit": 7.4,
    "allpay_trigger_policy": "Special-case All-Pay допустим только на фиксированном пакете или tie-break.",
    "if_allpay_triggered_max_cash_offer": 126.3,
    "cumulative_allpay_budget_remaining": 4880.0
  },
  "financial_breakdown": {
    "income": {},
    "expenses": {},
    "losses_and_risks": {},
    "result": {
      "net_profit": 126.3,
      "gross_profit_before_bid": 174.5,
      "net_profit_at_recommended_bid": 132.1
    }
  },
  "system_check": {
    "status": "supported",
    "critical_blocking_errors": [],
    "warnings": [],
    "optimization_hints": [],
    "topology_candidates": []
  }
}
```

Ключевые правила payload’а:
- для потребителей `decision_summary.floor_or_ceiling_type == "floor"`;
- для генерации, storage и infrastructure `decision_summary.floor_or_ceiling_type == "ceiling"`;
- потребительская выручка считается через fixed connection tariff per tick, а не `demand * tariff`;
- `direct_delta_profit`, `enabler_value` и `bundle_synergy_value` разделены явно;
- `system_check.topology_candidates` содержит shortlist network planner;
- `metrics.storage_value` включает `arbitrage`, `balancing`, `reserve`, `anti_dumping_support`;
- `metrics.market` опирается на anti-dumping cap `1.2 * useful_energy_(t-1) + 10`.

## Forecast canonical schema

Канонический 2026-forecast always uses `48` ticks and canonical series:
- `illumination`
- `wind_<channel>`
- `house_a`
- `house_b`
- `office`
- `factory`
- `hospital`
- `market_price`
- `sell_price`
- `balancing_penalty_price`

Raw CSV names не показываются как “красивые” названия без mapping. Импорт хранит raw-to-canonical mapping и блокирует evaluation, если forecast не покрывает нужные object types.

## Post-auction plan export

`GET /api/sessions/<session_id>/post-auction-plan`
возвращает JSON-шаблон пост-аукционного проектирования.

`GET /api/sessions/<session_id>/post-auction-plan.yaml`
возвращает YAML-экспорт того же шаблона.

Структура включает:
- `game`
- `auction_result`
- `inventory`
- `topology_candidates`
- `installation_priority`
- `object_models`
- `market_plan`
- `tick_model`
- `final_decision`
