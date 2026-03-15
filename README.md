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
5. Перейти в `Forecast`, загрузить CSV или оставить встроенный прогноз.
6. Перейти в `Lots`, выбрать лот, открыть `Lot Detail`.
7. Открыть `Quick Auction` и проверить hotkeys `1..9`, `E`, `R`, `B`.

## Актуальное поведение

- Единый формат чисел: целые значения отображаются без `.0` во всех основных экранах (dashboard, lots, lot detail, forecast, quick auction, system).
- Рабочая цена (`working_bid`) едина для стратегии, таблиц, карточки лота и quick auction; приоритет источника: `target_bid → cautious_bid → hard_ceiling_bid`.
- В quick auction кнопка `Купить` покупает лот сразу по цене из поля без confirm-step.
- Если поле цены в quick auction не меняли, туда подставляется актуальная рабочая цена.
- После покупки в quick auction автоматически запускается пересчёт и обновление shortlist.
- На forecast-странице диапазон тактов корректно отображает старт с `0` (например, `0–47 (48 периодов)`).
- В активном прогнозе и диагностике показываются только `mapped` колонки реального CSV (например, `house`, `office`, `factory`) без синтетических user-visible рядов (`class3` не выводится, если его нет в CSV).
- Strategy snapshot показывает комбинации в формате `Название (ID) + ...` и per-lot детализацию:
  - `<лот> (<id>) — цена: <...>, прибыль: <...>`
- В сценариях `Worst / Base / Best` используются разные пояснения, зависящие от метрик сценария.
- В `System` объекты сортируются по последнему добавлению (новые сверху), а рекомендации точки подключения учитывают потери и лимиты (capacity), занятость точки и допустимые альтернативы.

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
