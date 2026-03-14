# IES Web

`IES Web` - единое Flask web-приложение для анализа лотов ИЭС по прогнозу, управления сессиями и администрирования правил.

Официальный runtime у проекта один:

```bash
flask --app ies_bot_skeleton.web.app:create_app run
```

Публичных CLI-режимов `online`, `offline`, `lottool`, `fill-lots` у продукта нет.

## Требования

- Python 3.11+
- SQLite по умолчанию или любая БД, совместимая с SQLAlchemy и Flask-Migrate

## Установка

Из корня репозитория:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Настройка базы данных

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
flask db upgrade -d ies_bot_skeleton/web/migrations
flask seed
```

По умолчанию локальная SQLite БД создается как `ies_web.db` в корне проекта.

Seed-учетные записи:
- `admin / admin123`
- `analyst / analyst123`

После `flask seed` приложение получает два built-in сценария:
- `ies_test_game_2026` - preset тестовой игры по умолчанию;
- `ies_2026` - совместимый generic ruleset для ручных и тестовых сценариев.

## Запуск dev-server

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
export IES_WEB_ENV=development
flask run
```

Приложение будет доступно на `http://127.0.0.1:5000`.

## Основные разделы приложения

### Сессии
- создание и удаление сессий;
- новая сессия по умолчанию создаётся как preset "Тестовая игра" со стартовым пакетом и 5 аукционными лотами;
- выбор стратегии;
- обзор бюджета, купленных лотов и остатка бюджета;
- выбор активного прогноза;
- экспорт и импорт данных сессии.

### Прогноз
- загрузка CSV-прогнозов;
- нормализация CSV в канонические ключи факторов/профилей через mapping-layer (`auto-detect` + `column_map`);
- выбор активного прогноза сессии;
- fallback на встроенный прогноз тестовой игры, если пользовательский прогноз не выбран;
- диагностика качества данных и просмотр статистики по рядам;
- отчёт совместимости прогноза с типами объектов и лотами (покрытые, частично покрытые, отсутствующие типы, лишние колонки);
- строгая проверка горизонта: число тактов прогноза должно совпадать с `ruleset.time.horizon_ticks`.
- график ключевых рядов прогноза.

### Лоты
- список, просмотр, создание, редактирование и удаление лотов;
- визуальный редактор состава лота;
- покупка лота по фактической цене сделки;
- отклонение и восстановление лота;
- массовый пересчет оценок.

### Аналитика
- главная страница сессии как единый dashboard;
- оценка каждого лота по активному прогнозу в detail-page;
- quick auction как вспомогательный экран ускоренной оценки;
- учет купленного портфеля и остатка бюджета в каждом расчете;
- стратегия покупки: лучшие одиночные лоты, пары, группы и лучшая комбинация в бюджет;
- объяснимая синергия по формуле `Δ(A+B)-Δ(A)-Δ(B)`.

### Администрирование
- rulesets;
- start pack templates;
- object types;
- stale warnings после изменения правил, типов объектов, прогноза и портфеля.

## Built-in Preset "Тестовая Игра"

По умолчанию продукт использует отдельный built-in preset тестовой игры:
- `Ruleset.code = ies_test_game_2026`
- `StartPackTemplate.code = test_game_default`
- bundled forecast показывается пользователю как `Прогноз тестовой игры`
- новая сессия без явного `ruleset_id` создаётся уже заполненной:
  - стартовый пакет;
  - 5 аукционных лотов (`Теплый офис`, `Ветро-промышленный`, `Солнечный накопитель`, `Деловая мини-подстанция`, `Запасной накопитель с мини`);
  - встроенный прогноз без создания отдельной записи `Forecast` в БД.

Внутренние `ObjectType.code` (`cyber_solar`, `mini_substation_a`, `tps` и т.д.) сохранены ради совместимости доменной логики, adapter mapping и тестов.

## Новая модель оценки лотов

Оценка выполняется как маржинальный вклад в текущий портфель:

`ΔПрофит(портфель + лот) - ΔПрофит(портфель)`

Ключевые свойства:
- расчёт идёт по всем тактам активного прогноза;
- расчёт зависит от текущего купленного портфеля;
- роли объектов учитываются раздельно: `consumer`, `generator`, `storage`, `infrastructure`, `mixed`;
- в каждом результате есть `Worst/Base/Best` со стабильными полями:
  - `revenue_total`, `cost_total`, `penalties_total`, `losses_total`, `net_profit`, `utility_score`, `bid_ceiling`, `recommended_bid`, `explanation`;
- в `metrics_json` сохраняется структура:
  - `scenarios`, `decomposition`, `bids`, `portfolio_delta`, `forecast_compatibility`, `role_breakdown`, `synergy`;
- ставка рассчитывается тремя уровнями:
  - `cautious_bid`, `target_bid`, `hard_ceiling_bid`.

## Стратегия покупки (одиночные/пары/группы)

- одиночные лоты ранжируются по `risk-adjusted net profit` (`utility_score` используется как tie-break);
- пары и тройки считаются полным перебором в рамках бюджета;
- группы расширяются beam-search;
- для каждой рекомендации отображаются:
  - ожидаемая прибыль,
  - риск,
  - полезность,
  - синергия,
  - объяснение причины рекомендации.

## Каноническая схема прогноза

Прогноз хранится в 2 слоях:
- `factors_json`:
  - `wind_factor`, `solar_factor`, `market_price_buy`, `market_price_sell`, `fuel_price`, `temperature`, `time_of_day`;
- `profiles_json`:
  - `factory_load`, `office_load`, `house_load`, `solar_profile`, `wind_profile`, `storage_default_profile`.

Привязка объекта к прогнозу задаётся полями `ObjectType`:
- `forecast_profile_key`
- `resource_dependencies_json`
- `forecast_model_type`
- `economic_role`

### Поведение при неполном прогнозе

- система формирует отчёт совместимости;
- если совместимость неполная, оценка/пересчёт/стратегия блокируются на уровне сессии;
- API возвращает structured-ошибку `forecast_incompatible` (HTTP 409) с деталями отчёта.

## Покупка лотов и портфель

- Лот можно купить по фактической цене сделки.
- После покупки статус лота меняется на `Куплен`.
- Потраченный бюджет считается по сумме цен купленных лотов.
- Купленные лоты материализуются в портфель сессии и учитываются в дальнейшем анализе.
- Покупку можно отменить до появления отдельной механики финализации.

## Миграции и служебные Flask-команды

```bash
flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
flask --app ies_bot_skeleton.web.app:create_app seed
flask --app ies_bot_skeleton.web.app:create_app create-admin <username> <password>
```

Внутренняя команда `import-legacy` оставлена только как служебный механизм миграции старых данных и не является пользовательским workflow.

## API аналитики

- `POST /api/lots/<id>/evaluate`
- `GET /api/sessions/<id>/lots/analytics`
- `GET /api/sessions/<id>/strategy`
- `GET /api/sessions/<id>/forecast-compatibility`

Экспорт/импорт сессии использует `schema_version = 5`; импорт остаётся backward-compatible с `schema_version = 4`.

## Тесты

```bash
.venv/bin/python -m compileall -q ies_bot_skeleton tests
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/pytest -q
.venv/bin/python -m build
```

## Docker

```bash
docker compose up -d --build
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app seed
```

## Дополнительная документация

- [Архитектура web-first](docs/architecture/web_first.md)
- [Схемы данных](docs/data_schema.md)
- [Legacy import как внутренняя утилита](docs/internal/legacy_import.md)
