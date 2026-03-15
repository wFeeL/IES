# IES Web

Web-приложение для работы с игровыми сессиями ИЭС: сессии, прогнозы, лоты, quick auction, портфель, админка справочников.

## Запуск за 5 минут

### 1) Установить зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2) Поднять БД и seed

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
flask db upgrade -d ies_bot_skeleton/web/migrations
flask seed
```

### 3) Запустить сервер

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
export IES_WEB_ENV=development
flask run
```

Открыть: `http://127.0.0.1:5000`

Логины после seed:
- `admin / admin123`
- `analyst / analyst123`

## Первый сценарий проверки

1. Войти под `admin`.
2. Открыть `/dashboard`.
3. Создать сессию.
4. Открыть сессию (`/sessions/<id>`).
5. Перейти в `Forecast`, загрузить CSV и проверить блоки `Raw CSV columns`, `Mapping`, `Лишние колонки CSV`.
6. Убедиться, что активный прогноз покрывает типы объектов и не блокирует оценку лотов.
7. Перейти в `Lots`, открыть `Lot Detail` и проверить `Worst / Base / Best`, `system-check` и рабочую цену.
8. Открыть `Quick Auction`, проверить hotkeys `1..9`, `E`, `R`, `B` и купить лот без confirm-step.
9. Убедиться, что после покупки сразу обновились бюджет, shortlist и strategy snapshot.

## Актуальное поведение

- Единый формат чисел: целые значения отображаются без `.0` во всех основных экранах (dashboard, lots, lot detail, forecast, quick auction, system).
- Рабочая цена (`working_bid`) едина для стратегии, таблиц, карточки лота и quick auction и строится из новой valuation-модели:
  - `cautious_bid` - консервативный вход для слабого/рискованного сценария;
  - `target_bid` - основная экономически оправданная ставка;
  - `hard_ceiling_bid` - предельная цена, выше которой аукцион теряет смысл;
  - `budget_adjusted_bid` - целевая ставка, ограниченная текущим остатком бюджета;
  - `working_bid` - пользовательская цена для покупки, уже учитывающая риск-band, бюджет, portfolio synergy и system fit; она может быть ниже `target_bid`, если модель оставляет риск-буфер.
- `working_bid` не остаётся положительным при неположительной взвешенной маржинальной прибыли: если ставка равна `0`, UI обязан показать честную причину через `working_bid_reason`.
- Оценка лота считается как маржинальный вклад к текущему портфелю на всём горизонте активного прогноза, а не как статическое число из summary.
- После покупки лота рабочие цены и стратегия остальных лотов пересчитываются на новом контексте портфеля.
- В quick auction кнопка `Купить` покупает лот сразу по цене из поля без confirm-step.
- Покупка лота блокируется, если активный прогноз несовместим, и в API, и в SSR-форме покупки.
- Если поле цены в quick auction не меняли, туда подставляется актуальная рабочая цена.
- Оценка в quick auction не меняет `current_bid`: рыночная ставка и цена покупки разделены.
- После покупки в quick auction автоматически запускается пересчёт и обновление:
  - бюджета;
  - shortlist;
  - стратегий `Current best / Plan B / Plan C / After purchase / After loss`.
- Budget snapshot (`budget / spent / remaining`) синхронизирован между workbench, forecast, lots, lot detail и quick auction через единый session hero.
- На forecast-странице и в active forecast на dashboard горизонт показывается один раз в одном формате, например `0–47 (48 периодов)`.
- В forecast UI данные разведены по слоям:
  - `Raw CSV columns` - реальные английские названия колонок из файла;
  - `Mapping` - явный мост `raw_name -> interpreted meaning`;
  - `Лишние колонки CSV` - колонки CSV, которые не участвуют в модели.
- В диагностике прогноза дополнительно показываются:
  - покрытие объектов и профилей;
  - short horizon / missing / partial coverage;
  - реально используемые raw columns без подстановки выдуманных имён.
- Strategy snapshot показывает комбинации в формате `Название (ID) + ...`, per-lot детализацию и три сценарных среза:
  - `full_budget`;
  - `after_purchase`;
  - `after_loss`.
- Для каждой комбинации доступны:
  - `<лот> (<id>) — цена: <...>, прибыль: <...>`
- `display_title`, `total_price`, `total_profit`, `synergy`, `utility`, `budget_fit`, `explanation`.
- В сценариях `Worst / Base / Best` используются разные пояснения, зависящие от метрик сценария.
- В `Lot Detail` сценарная ставка явно помечена как неоперационная (`Сценарный потолок ставки`), чтобы не путать её с реальной ценой покупки.
- В `System` и `Object Edit` рекомендации точки подключения учитывают:
  - потери;
  - лимиты по точкам подключения;
  - запас по мощности;
  - slot headroom;
  - альтернативные точки;
  - связь рекомендации с forecast profile и forecast model.
- В `Lot Detail` system-check встроен прямо в оценку лота и влияет на `system_fit_score` и рабочую цену.
- Циклы и разрывы до главной подстанции блокируются на write-path (`/api/objects/*` и SSR-редактор объектов).
- Если топология сети некорректна, lot evaluation возвращает `system_check.status=blocked` и обнуляет рабочую цену.

## Product flow

1. Загрузить CSV прогноза и проверить `Raw CSV columns`, `Mapping`, `Лишние колонки CSV`.
2. Проверить object coverage и убедиться, что прогноз совместим с текущими объектами и лотами.
3. Открыть `Lots` или `Lot Detail` и сравнить `Worst / Base / Best`, декомпозицию прибыли, `portfolio synergy` и `system-check`.
4. Использовать `Quick Auction` для фактической покупки по `working_bid`.
5. После покупки перейти к новому shortlist и к пересчитанной стратегии на остаток бюджета.
6. Проверить `System` и `Object Edit`, если лот/объект требует другой точки подключения.

## Что где лежит

- `ies_bot_skeleton/web/app.py` — Flask app factory.
- `ies_bot_skeleton/web/routes/` — SSR + API роуты.
- `ies_bot_skeleton/web/services/` — бизнес-логика web-слоя.
- `ies_bot_skeleton/web/templates/` — Jinja templates.
- `ies_bot_skeleton/web/static/` — CSS/JS.
- `ies_bot_skeleton/web/migrations/` — миграции БД.
- `ies_bot_skeleton/application/` — use-cases.
- `ies_bot_skeleton/domain/` — доменные модули.
- `tests/` — тесты.

## Ключевые страницы

- `/dashboard` — список и создание сессий.
- `/sessions/<id>` — workbench сессии.
- `/forecast/<id>` — центр прогноза.
- `/lots/<id>` — список лотов.
- `/lots/item/<lot_id>` — детальная страница лота.
- `/quick-auction/<id>` — быстрый аукцион.
- `/system/<id>` — обзор энергосистемы.

## Ключевые API

- `POST /api/lots/<id>/evaluate`
- `GET /api/sessions/<id>/lots/analytics`
- `GET /api/sessions/<id>/strategy`
- `POST /api/forecast/upload`
- `GET /api/sessions/<id>/forecast-compatibility`

Полный контракт: [docs/data_schema.md](docs/data_schema.md)

## Что считать важным в данных

- `working_bid` - основная пользовательская цена покупки.
- `budget_adjusted_bid` - ceiling по бюджету, не итоговая пользовательская цена.
- `system_check` - сетевой комментарий к лоту или объекту: лучшая точка, альтернативы, потери, ограничения.
- `portfolio_synergy` - насколько лот усиливает или ослабляет уже собранный портфель относительно standalone-эффекта.
- `raw_csv_columns / used_raw_columns / unsupported_raw_columns` - базовая тройка для интерпретации прогноза без смешения raw и canonical имён.

## Разработка

### Базовый цикл

```bash
source .venv/bin/activate
flask --app ies_bot_skeleton.web.app:create_app run
```

### Тесты

```bash
.venv/bin/pytest -q
```

### Рекомендуемый pre-merge набор

```bash
.venv/bin/python -m compileall -q ies_bot_skeleton tests
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/pytest -q
.venv/bin/python -m build
```

## Миграции

```bash
flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
```

## Docker

```bash
docker compose up -d --build
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app seed
```

## Документация

- [Архитектура web-first](docs/architecture/web_first.md)
- [Схемы данных](docs/data_schema.md)
- [Legacy import (internal)](docs/internal/legacy_import.md)
