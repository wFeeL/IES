# Web-First Architecture

IES поставляется как одно Flask web-приложение.

## Публичный runtime

Единственный поддерживаемый способ запуска:

```bash
flask --app ies_bot_skeleton.web.app:create_app run
```

Пользовательские сценарии выполняются через SSR-страницы и внутренний JSON API.

## Структура

- `ies_bot_skeleton/web/` - Flask app, ORM, routes, forms, templates, static
- `ies_bot_skeleton/application/` - use-cases для сессий, лотов, портфеля, анализа, рекомендаций и импорта
- `ies_bot_skeleton/domain/lot_analysis/` - доменная логика скоринга, прогноза, сети и auction EV
- `ies_bot_skeleton/resources/` - встроенный прогноз тестовой игры и internal import fixtures

## Ключевые принципы

- Никаких публичных CLI-режимов `online`, `offline`, `lottool`, `fill-lots`
- Никакой регистрации роутов через import side effects
- SSR и JSON API используют одну и ту же бизнес-логику
- Анализ всегда выполняется по прогнозу
- Если пользовательский прогноз не выбран, используется встроенный прогноз тестовой игры
- Прогноз нормализуется в canonical schema (factors/profiles) перед расчётом
- Совместимость прогноза с типами объектов проверяется на уровне сессии
- При несовместимом прогнозе оценка и стратегия блокируются (API `forecast_incompatible`, HTTP 409)
- Портфель купленных лотов участвует в последующей аналитике
- Стратегия покупки рассчитывает одиночные лоты, пары и группы с синергией `Δ(A+B)-Δ(A)-Δ(B)`
- Отдельного compare-flow в публичном продукте нет
- Legacy SSR-маршруты `/recommend/<session_id>`, `/strategy-fit/<lot_id>`, `/evaluation/<session_id>` сохранены только для совместимости и редиректят в основной dashboard-поток
