# Data Schemas

## Session export / import JSON

Экспорт сессии из web-приложения возвращает JSON-структуру, пригодную для повторного импорта.

```json
{
  "schema_version": 5,
  "session": {
    "title": "Тестовая игра",
    "selected_strategy": "balanced",
    "selected_forecast_id": 3,
    "budget_total": 200.0
  },
  "objects": [],
  "lots": [],
  "forecasts": [],
  "evaluations": []
}
```

Ключевые поля:
- `schema_version` - версия схемы импорта и экспорта;
- `session.selected_forecast_id` - активный пользовательский прогноз сессии, если выбран;
- `session.budget_total` - общий бюджет сессии;
- новая сессия без явного `ruleset_id` по умолчанию создаётся на built-in preset `ies_test_game_2026`;
- default session bootstrap добавляет стартовый пакет и 5 аукционных лотов тестовой игры;
- старые поля `analysis_mode` и `corridor_settings` при импорте игнорируются для backward compatibility.
- импорт поддерживает `schema_version=4` и `schema_version=5`.

## Lot payload

Лот хранится как SQLAlchemy-модель и сериализуется через web API:

```json
{
  "id": 12,
  "session_id": 5,
  "name": "Wind + Storage",
  "scope": "normal",
  "status": "available",
  "base_bid": 120.0,
  "current_bid": 130.0,
  "purchase_price": null,
  "purchased_at": null,
  "note": "",
  "available_round": 1,
  "items": [
    {
      "object_type_id": 4,
      "object_type_code": "wind",
      "quantity": 1,
      "overrides": {}
    }
  ]
}
```

Состав лота валидируется на сервере:
- `object_type_id` должен существовать и быть активным;
- `quantity >= 1`;
- `overrides` должен быть объектом;
- пустой лот не допускается.

## Evaluation payload

Оценка лота возвращает расширенную прогнозную аналитику:

```json
{
  "summary_score": 123.4,
  "forecast_context": {
    "source": "selected_forecast",
    "source_label": "Пользовательский прогноз",
    "forecast_id": 3,
    "forecast_name": "Прогноз игры #1",
    "tick_from": 1,
    "tick_to": 48,
    "periods_count": 48
  },
  "portfolio_context": {
    "bought_lots_count": 2,
    "spent_total": 130.0,
    "remaining_budget": 70.0,
    "owned_objects_count": 9
  },
  "scenario_breakdown": {
    "worst": {
      "revenue_total": 0.0,
      "cost_total": 0.0,
      "penalties_total": 0.0,
      "losses_total": 0.0,
      "net_profit": 0.0,
      "utility_score": 0.0,
      "bid_ceiling": 0.0,
      "recommended_bid": 0.0,
      "explanation": ""
    },
    "base": {},
    "best": {}
  },
  "financial_breakdown": {
    "income": {},
    "expenses": {},
    "losses_and_risks": {},
    "result": {},
    "ui_rows": [
      {
        "key": "entry_price",
        "label": "Цена входа",
        "value": 100.0,
        "group": "expense",
        "emphasis": false
      }
    ]
  },
  "decision_summary": {
    "cautious_bid": 40.0,
    "target_bid": 52.0,
    "hard_ceiling_bid": 58.0,
    "soft_bid": 40.0,
    "hard_bid": 52.0,
    "stop_bid": 58.0
  },
  "metrics": {
    "scenarios": {},
    "decomposition": {},
    "bids": {
      "valuation_model": {
        "model": "valuation_model_v2",
        "profile": "balanced",
        "risk_band": "low|medium|high",
        "p_worst": 0.0,
        "p_base": 0.0,
        "p_best": 0.0,
        "p_exp": 0.0,
        "risk_ratio": 0.0,
        "risk_premium": 0.0,
        "v1": 0.0,
        "v2": 0.0,
        "blend": 0.0,
        "cautious_bid": 0.0,
        "target_bid": 0.0,
        "hard_ceiling_bid": 0.0,
        "budget_limited_bid": 0.0
      }
    },
    "portfolio_delta": {},
    "forecast_compatibility": {},
    "role_breakdown": {},
    "synergy": {}
  },
  "reasons": [],
  "risk_commentary": "",
  "strategy_fit_text": "",
  "is_stale": false,
  "stale_reason": ""
}
```

Ключевые правила:
- анализ всегда выполняется по активному прогнозу;
- если пользовательский прогноз не выбран, используется built-in `Прогноз тестовой игры`;
- отдельного режима без прогноза и отдельного compare-flow в схеме продукта нет.
- исторические `evaluations` сохраняются в БД и экспорте, но не отображаются отдельным экраном в основном пользовательском UX.
- `financial_breakdown.ui_rows` предназначен для UI: содержит только релевантные/ненулевые строки (`abs(value) > 1e-6`) + обязательные `entry_price` и `net_profit`.

## Forecast canonical schema

`ForecastPeriod` хранит канонические ряды:
- `factors_json`: `wind_factor`, `solar_factor`, `market_price_buy`, `market_price_sell`, `fuel_price`, `temperature`, `time_of_day`;
- `profiles_json`: `factory_load`, `office_load`, `house_load`, `solar_profile`, `wind_profile`, `storage_default_profile`.

`Forecast` хранит служебные поля нормализации и совместимости:
- `normalization_map_json`
- `compatibility_report_json`
- `is_compatible`
- `incompatibility_reason`

`ObjectType` хранит поля прогностической привязки:
- `forecast_profile_key`
- `resource_dependencies_json`
- `forecast_model_type`
- `economic_role`

Forecast summary (SSR/API) дополнительно содержит display-слой для UI:
- `load_series_display[]`:
  - `key`, `label`, `avg`, `is_service`;
- `series_stats_display[]`:
  - `key`, `label`, `group` (`factor|profile|load|load_service`), `stats`.

Legacy-поля (`load_series`, `series_stats`) сохранены для backward compatibility.

## Strategy payload additions

`GET /api/sessions/<id>/strategy` возвращает прежние поля, плюс аддитивно:

```json
{
  "best_pairs": [
    {
      "lot_ids": [1, 2],
      "lot_bid_breakdown": [
        {
          "lot_id": 1,
          "lot_name": "Лот A",
          "standalone_target_bid": 10.0,
          "standalone_hard_ceiling_bid": 12.0,
          "allocated_target_bid": 11.2,
          "allocated_cautious_bid": 8.6,
          "allocated_hard_ceiling_bid": 13.1,
          "synergy_allocated": 1.2,
          "budget_adjusted_bid": 9.8
        }
      ]
    }
  ]
}
```

Built-in preset значения:
- `Ruleset.code = "ies_test_game_2026"`
- `StartPackTemplate.code = "test_game_default"`
- bundled forecast не хранится как строка `Forecast` в БД и используется как fallback для новых test-game sessions
- внутренние `ObjectType.code` не переименовываются даже если пользовательские названия выровнены под тестовую игру
