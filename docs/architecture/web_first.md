# Web-First Architecture

IES поставляется как одно Flask web-приложение.

## Публичный runtime

Единственный поддерживаемый способ запуска:

```bash
flask --app ies_bot_skeleton.web.app:create_app run
```

Пользовательские сценарии выполняются через SSR-страницы и внутренний JSON API.

## Структура

- `ies_bot_skeleton/web/` — Flask app, ORM, routes, forms, templates, static
- `ies_bot_skeleton/application/` — use-cases для lot CRUD, анализа, рекомендаций и импорта
- `ies_bot_skeleton/domain/lot_analysis/` — доменная логика скоринга, прогноза, сети и auction EV
- `ies_bot_skeleton/resources/` — встроенные bundled forecasts и internal import fixtures

## Ключевые принципы

- Никаких публичных CLI-режимов `online/offline/lottool/fill-lots`
- Никакой регистрации роутов через import side effects
- SSR и JSON API используют одну и ту же бизнес-логику
- `forecast` без выбранного пользовательского прогноза использует bundled forecast
- `no_forecast` использует ручной коридор неопределённости
