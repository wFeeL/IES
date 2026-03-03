# Data Schemas

## Online `state.json`

Файл сохраняется в корне запуска online-режима.

```json
{
  "wind_k": { "M12": 0.041 },
  "solar": {
    "best_angle": { "S1": { "13": 80 } },
    "best_power": { "S1": { "13": 12.4 } }
  },
  "last_solar_angle": { "S1": 75 },
  "printed_once": true,
  "schema_version": 2,
  "season": "2026",
  "created_at": "2026-03-03T10:00:00+00:00",
  "updated_at": "2026-03-03T10:00:03+00:00"
}
```

Поля:
- `schema_version` — версия схемы (`2`).
- `season` — зафиксированный compat сезон состояния.
- `created_at`, `updated_at` — UTC ISO timestamps.

Миграции:
- v1 -> v2: добавляются `schema_version`, `season`, `created_at`, `updated_at`.
- при загрузке старая версия мигрируется автоматически.

## Lot JSON (`lot_tool/data/lots/*.json`)

```json
{
  "lot_id": "L12",
  "title": "Lot L12",
  "note": "",
  "items": [
    {
      "kind": "wind",
      "id": "W1",
      "qty": 1,
      "contract_rub_per_tick": 0.0,
      "tariff_rub_per_mw_tick": 0.0,
      "meta": {}
    }
  ],
  "suggested_bid": 120.0
}
```

Нормализация `offline fill-lots`:
- добавляет `lot_id` и `title`, если отсутствуют;
- для каждого `item` гарантирует `kind`, `id`, `qty >= 1`, `contract_rub_per_tick`, `tariff_rub_per_mw_tick`, `meta`.

