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

## Offline `lot_tool/data/state.json`

```json
{
  "schema_version": 1,
  "game": { "ticks_per_day": 48, "horizon_ticks": 48 },
  "budget": { "cash": 9999, "allpay_spent": 0 },
  "owned_lots": [],
  "owned_objects_override": [],
  "network_plan": { "mode": "branches", "branches": [] },
  "assumptions": {
    "pwin_default": 0.35,
    "risk_mode": "conservative",
    "storage_soc_init_fraction": 0.5,
    "corridor": { "wind_mul": 0.1, "solar_mul": 0.1, "load_mul": 0.1 }
  }
}
```

Поля:
- `schema_version` — версия схемы offline-state (текущая `1`).
- `assumptions.storage_soc_init_fraction` — стартовый SOC для всех накопителей (0..1).
- `assumptions.storage_soc_init` — альтернатива в абсолютных единицах (MW*tick).

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
