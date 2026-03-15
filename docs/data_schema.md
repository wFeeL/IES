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
    "budget_adjusted_bid": 52.0,
    "budget_remaining": 70.0,
    "expected_net_profit": 64.0,
    "risk_adjusted_net_profit": 41.5,
    "model_working_bid": 47.5,
    "portfolio_synergy": 4.2,
    "system_fit_score": 3.6,
    "working_bid": 47.5,
    "working_bid_source": "target|budget_adjusted|cautious|zero",
    "working_bid_reason": "..."
  },
  "metrics": {
    "scenarios": {},
    "decomposition": {},
    "bids": {
      "valuation_model": {
        "model": "valuation_model_v3",
        "profile": "consumer|generator|storage|infrastructure|mixed",
        "risk_band": "low|medium|high",
        "p_worst": 0.0,
        "p_base": 0.0,
        "p_best": 0.0,
        "p_exp": 0.0,
        "risk_ratio": 0.0,
        "risk_premium": 0.0,
        "role_multipliers": {
          "target": 1.0,
          "cautious": 1.0,
          "ceiling": 1.0
        },
        "portfolio_synergy": 0.0,
        "system_fit_score": 0.0,
        "anchor_value": 0.0,
        "synergy_bonus": 0.0,
        "system_bonus": 0.0,
        "cautious_bid": 0.0,
        "target_bid": 0.0,
        "hard_ceiling_bid": 0.0,
        "budget_adjusted_bid": 0.0,
        "working_bid": 0.0
      }
    },
    "portfolio_delta": {},
    "forecast_compatibility": {},
    "role_breakdown": {},
    "synergy": {
      "score": 0.0,
      "standalone_expected_net_profit": 0.0,
      "marginal_expected_net_profit": 0.0
    },
    "system_check": {
      "status": "supported|neutral|risky|blocked",
      "message": "...",
      "items": []
    }
  },
  "reasons": [],
  "risk_commentary": "",
  "strategy_fit_text": "",
  "system_check": {
    "status": "supported|neutral|risky|blocked",
    "message": "...",
    "items": []
  },
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
- `working_bid` - основная пользовательская цена для покупки, а не alias `target_bid`.
- `budget_adjusted_bid` - потолок по бюджету, полученный из `target_bid` и текущего `budget_remaining`.
- `model_working_bid` - внутренний риск-буфер между `target_bid` и итоговой рабочей ценой; итоговый `working_bid` берётся из него, если budget/экономика не требуют более жёсткого ограничения.
- `portfolio_synergy` и `system_fit_score` входят в valuation model и влияют на `working_bid`.
- если `working_bid == 0`, это честно отражается через `working_bid_reason`, а не замещается старым fallback-числом; типичный повод - неположительная взвешенная маржинальная прибыль или исчерпанный бюджет.

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
- `raw_csv_columns[]` - реальные заголовки CSV;
- `used_raw_columns[]` - raw-колонки, реально участвующие в модели;
- `unsupported_raw_columns[]` - raw-колонки, которые не используются;
- `column_mapping_rows[]`:
  - `raw_name`
  - `canonical_key`
  - `mapped_kind` (`factor|profile`)
  - `interpreted_meaning`
- `mapped_raw_stats_display[]`:
  - статистика именно по использованным raw-колонкам;
- `object_coverage_rows[]`:
  - `object_type_code`
  - `object_type_name`
  - `status` (`covered|partial|missing`)
  - `required_profiles`
  - `required_factors`
  - `missing_profiles`
  - `missing_factors`
  - `partial_profiles`
  - `partial_factors`

UI-правило:
- в user-facing forecast блоках не должны появляться выдуманные raw-имена;
- raw и canonical имена показываются только через явный mapping.

## Strategy payload additions

`GET /api/sessions/<id>/strategy` возвращает каталог комбинаций и сценарные пересчёты:

```json
{
  "plan_b": {},
  "plan_c": {},
  "scenarios": {
    "full_budget": {
      "title": "Полный бюджет",
      "best_combination": {}
    },
    "after_purchase": {
      "title": "После покупки лучшей комбинации",
      "best_combination": {}
    },
    "after_loss": {
      "title": "После потери лучшего лота",
      "best_combination": {}
    }
  },
  "best_pairs": [
    {
      "lot_ids": [1, 2],
      "display_title": "Лот A (1) + Лот B (2)",
      "total_price": 19.8,
      "total_profit": 31.4,
      "synergy": 3.1,
      "utility": 27.9,
      "budget_fit": {
        "is_affordable": true,
        "remaining_budget": 70.0,
        "headroom": 50.2
      },
      "explanation": "...",
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

Смысл полей:
- `best_singles`, `best_pairs`, `best_groups` - лучшие комбинации в текущем сценарии;
- `plan_b`, `plan_c` - альтернативы, если лучший план недоступен;
- `scenarios.after_purchase` - стратегия на остаток бюджета после гипотетической покупки лучшей комбинации;
- `scenarios.after_loss` - стратегия после потери лучшего одиночного лота.

Built-in preset значения:
- `Ruleset.code = "ies_test_game_2026"`
- `StartPackTemplate.code = "test_game_default"`
- bundled forecast не хранится как строка `Forecast` в БД и используется как fallback для новых test-game sessions
- внутренние `ObjectType.code` не переименовываются даже если пользовательские названия выровнены под тестовую игру
