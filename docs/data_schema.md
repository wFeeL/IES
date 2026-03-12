# Data Schemas

## Session export / import JSON

Экспорт сессии из web-приложения возвращает JSON-структуру, пригодную для повторного импорта.

```json
{
  "schema_version": 2,
  "session": {
    "title": "Demo",
    "selected_strategy": "balanced",
    "analysis_mode": "forecast",
    "selected_forecast_id": 3,
    "corridor_settings": {
      "consumer_load_pct": 10.0,
      "producer_generation_pct": 10.0,
      "solar_output_pct": null,
      "wind_output_pct": null
    }
  },
  "objects": [],
  "lots": [],
  "forecasts": [],
  "evaluations": []
}
```

Ключевые поля:
- `schema_version` — версия схемы импорта/экспорта;
- `session.analysis_mode` — `forecast` или `no_forecast`;
- `session.selected_forecast_id` — активный прогноз сессии, если выбран;
- `session.corridor_settings` — ручной коридор для режима без прогноза.

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
