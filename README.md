# IES Web

`IES Web` — единое Flask web-приложение для анализа лотов ИЭС, работы с прогнозами, сравнения, рекомендаций и администрирования правил.

Официальный runtime у проекта один:

```bash
flask --app ies_bot_skeleton.web.app:create_app run
```

CLI-режимы `online`, `offline`, `lottool`, `fill-lots` и legacy-лаунчеры в продукт не входят.

## Требования

- Python 3.11+
- SQLite по умолчанию или любая БД, совместимая с SQLAlchemy/Flask-Migrate

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

По умолчанию локальная SQLite БД создаётся как `ies_web.db` в корне проекта.

Seed-учётные записи:
- `admin / admin123`
- `analyst / analyst123`

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
- выбор стратегии;
- настройка режима анализа;
- сохранение рабочего контекста в рамках сессии.

### Лоты
- список, просмотр, создание, редактирование и удаление лотов;
- визуальный редактор состава лота;
- серверная валидация состава и параметров.

### Прогнозы
- загрузка CSV-прогнозов;
- выбор активного прогноза сессии;
- fallback на встроенный bundled forecast, если пользовательский прогноз не выбран.

### Аналитика
- оценка конкретного лота;
- сравнение лотов;
- рекомендации по лучшему лоту;
- `quick auction` и `strategy fit`.

### Администрирование
- rulesets;
- start pack templates;
- object types;
- stale warnings после изменения правил и типов объектов.

## Режимы анализа

Приложение поддерживает два режима:
- `Без прогноза` — используется ручной коридор неопределённости;
- `С прогнозом` — используется выбранный прогноз сессии или встроенный bundled forecast.

Контекст анализа одинаково интерпретируется в SSR и JSON API.

## Миграции и служебные Flask-команды

```bash
flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
flask --app ies_bot_skeleton.web.app:create_app seed
flask --app ies_bot_skeleton.web.app:create_app create-admin <username> <password>
```

Внутренняя команда `import-legacy` оставлена только как служебный механизм миграции старых данных и не является пользовательским workflow.

## Тесты

```bash
python -m compileall -q ies_bot_skeleton tests
ruff check .
black --check .
pytest -q
python -m build
```

## Docker

```bash
docker compose up -d --build
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app db upgrade -d ies_bot_skeleton/web/migrations
docker compose exec ies-web flask --app ies_bot_skeleton.web.app:create_app seed
```

## Дополнительная документация

- [Архитектура web-first](docs/architecture/web_first.md)
- [Legacy import как внутренняя утилита](docs/internal/legacy_import.md)
