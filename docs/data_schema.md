# Data Schemas

## Session export / import JSON

Экспорт сессии из web-приложения возвращает JSON-структуру, пригодную для повторного импорта.

```json
{
  "schema_version": 4,
  "session": {
    "title": "Demo",
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
- старые поля `analysis_mode` и `corridor_settings` при импорте игнорируются для backward compatibility.

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
    "forecast_name": "Forecast 1",
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
    "worst": {},
    "base": {},
    "best": {}
  },
  "financial_breakdown": {
    "income": {},
    "expenses": {},
    "losses_and_risks": {},
    "result": {}
  },
  "decision_summary": {
    "soft_bid": 40.0,
    "hard_bid": 52.0,
    "stop_bid": 58.0
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
- если пользовательский прогноз не выбран, используется встроенный базовый прогноз;
- отдельного режима без прогноза и отдельного compare-flow в схеме продукта нет.
- исторические `evaluations` сохраняются в БД и экспорте, но не отображаются отдельным экраном в основном пользовательском UX.
