# Web-First Architecture

IES поставляется как одно Flask web-приложение. Поддерживаемый пользовательский runtime остаётся web-first: SSR-страницы и внутренний JSON API работают поверх одного и того же application/domain слоя.

## Публичный runtime

```bash
flask --app ies_bot_skeleton.web.app:create_app run
```

CLI-режимы не являются публичным продуктовым интерфейсом. Основной пользовательский flow:
- сессия;
- прогноз;
- объекты и сеть;
- лоты и аукцион;
- strategy snapshot;
- post-auction planning export.

## Основные слои

- `ies_bot_skeleton/web/`
  Flask app, SQLAlchemy models, forms, routes, SSR templates, static assets.
- `ies_bot_skeleton/application/`
  use-cases для сессий, лотов, объектов, портфеля, анализа и импорта.
- `ies_bot_skeleton/domain/ies2026/`
  ruleset-aware 2026 domain: config, typed entities, forecast normalization, network validation/planning, delta-profit engine.
- `ies_bot_skeleton/resources/`
  built-in forecast, ruleset fixtures и reusable templates.
- `docs/`
  product-facing architecture notes, data schema, post-auction template documentation.

## Flow данных

1. Пользователь создаёт сессию и выбирает strategy profile.
2. `application.sessions` сохраняет сессию и выбранную стратегию.
3. `web.services.analysis_context` разрешает активный прогноз и бюджетный контекст.
4. `web.services.evaluation` адаптирует лот в canonical 2026-объекты.
5. `domain.ies2026.engine` считает `expected_delta_profit`, floor/ceiling, market breakdown и risks.
6. `domain.ies2026.network` строит shortlist допустимых topology candidates и validation issues.
7. SSR/UI показывает консолидированный analysis без дублирующей бизнес-логики в шаблонах.
8. `web.services.post_auction_plan` экспортирует текущую сессию в JSON/YAML шаблон для пост-аукционного проектирования.

## Что считается строго по правилам 2026

- horizon `48` ticks;
- consumer fixed tariff per tick, а не `demand * tariff`;
- anti-dumping cap `1.2 * useful_energy_(t-1) + 10`;
- полезная энергия считается после потерь;
- own generation продаётся на бирже, непроданный остаток уходит через GP fallback;
- storage limits `120 / 15 / 20`;
- one main substation rule;
- tree validation: no cycles, no islands, path to main, hospital dual input, factory warning, no mixed districts.

## Что остаётся параметризуемым

- demand elasticity model;
- loss approximation;
- wind calibration hooks;
- market clearing approximation внутри aggregate exchange model;
- strategy overlays для `balanced`, `generation`, `consumer`, `storage`, `eco`, `risk_averse`, `aggressive`.

## Почему архитектура остаётся web-first

- SSR и JSON API используют один и тот же `application` и `domain/ies2026` слой.
- Нет второго отдельного auction engine “для UI”.
- Нет отдельного скрытого batch-flow, который считает иначе, чем web.
- Strategy snapshot, quick auction и post-auction export читают ту же сессию и те же ruleset/config данные.
