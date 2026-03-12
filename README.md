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
- обзор бюджета, купленных лотов и остатка бюджета;
- выбор активного прогноза;
- экспорт и импорт данных сессии.

### Прогноз
- загрузка CSV-прогнозов;
- выбор активного прогноза сессии;
- fallback на встроенный базовый прогноз, если пользовательский прогноз не выбран;
- диагностика качества данных и просмотр статистики по рядам;
- график ключевых рядов прогноза.

### Лоты
- список, просмотр, создание, редактирование и удаление лотов;
- визуальный редактор состава лота;
- покупка лота по фактической цене сделки;
- отклонение и восстановление лота;
- массовый пересчет оценок.

### Аналитика
- оценка конкретного лота по активному прогнозу;
- рекомендации по лучшему доступному лоту;
- `quick auction` и `strategy fit`;
- учет уже купленного портфеля и оставшегося бюджета.

### Администрирование
- rulesets;
- start pack templates;
- object types;
- stale warnings после изменения правил, типов объектов, прогноза и портфеля.

## Принципы анализа

- Анализ всегда выполняется по активному прогнозу.
- Если пользовательский прогноз не выбран, используется встроенный базовый прогноз.
- Отдельного режима `Без прогноза` нет.
- Отдельного compare-flow нет: решение принимается по детальной аналитике конкретного лота, рекомендациям и ranking-таблицам.

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
- [Схемы данных](docs/data_schema.md)
- [Legacy import как внутренняя утилита](docs/internal/legacy_import.md)
