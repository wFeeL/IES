# IES Web 2026

Web-first инструмент для моделирования ИЭС 2026: лоты, тарифный аукцион, энергосистема, прогноз, unified lot optimizer и post-auction planning.

## Что реализовано

- ruleset 2026 на существующей Flask-архитектуре без второго параллельного engine;
- `25` детерминированно-случайных лотов тестовой игры;
- единый `unified lot optimizer` без ручного strategy-switching в основном UX;
- delta-profit valuation по `48` тактам;
- consumer fixed tariff per tick, а не `demand * tariff`;
- anti-dumping cap `1.2 * useful_energy_(t-1) + 10`;
- market model: exchange sale, GP fallback, GP purchase, balancing penalty;
- network validator: tree, path to main, no islands, no mixed districts, hospital dual input, factory warning;
- robust wind valuation: prior/posterior по скрытым thresholds, storm shutdown, hysteresis и inertia;
- singles / pairs / groups catalog с `optimal_purchase_price`, expected profit и risk-adjusted ranking;
- post-auction JSON/YAML template для дальнейшего моделирования.

## Основные модули

- `ies_bot_skeleton/domain/ies2026/config.py`
  ruleset defaults, anti-dumping, storage/network constraints, installation priority.
- `ies_bot_skeleton/domain/ies2026/types.py`
  typed 2026 entities и payloads.
- `ies_bot_skeleton/domain/ies2026/forecast.py`
  canonical 48-tick forecast normalization.
- `ies_bot_skeleton/domain/ies2026/network.py`
  topology planner и validator.
- `ies_bot_skeleton/domain/ies2026/engine.py`
  marginal value / delta-profit, floor/ceiling, storage decomposition, market simulation.
- `ies_bot_skeleton/web/services/evaluation.py`
  web adapter над domain engine.
- `ies_bot_skeleton/web/services/strategy.py`
  strategy snapshot: singles, pairs, groups, plan B, plan C.
- `ies_bot_skeleton/web/services/post_auction_plan.py`
  export post-auction system plan.

## Правила 2026, которые считаются строго

- один `main_substation`;
- consumer tariff = fixed connection tariff per tick;
- generator/storage/infrastructure bids use service tariff ceiling logic;
- storage `capacity=120`, `charge_rate=15`, `discharge_rate=20`;
- anti-dumping: `max_declared_sale_t = 1.2 * useful_energy_(t-1) + 10`;
- useful energy считается после сетевых потерь;
- all-pay не является основным режимом и применяется только как special-case для fixed package / tie-break;
- cumulative all-pay budget cap = `5000`;
- network must be a tree without cycles and islands;
- hospital requires two inputs;
- factory allows one or two inputs, one input gives warning.
- Покупка лота блокируется, если активный прогноз несовместим с составом лота.
- покупка требует явного checkbox-confirmation цены сделки в quick auction.
- Оценка в quick auction не меняет `current_bid`.
- Циклы и разрывы до главной подстанции блокируются на write-path.

## Что параметризовано

- demand elasticity model;
- loss approximation;
- wind curve calibration hooks;
- aggregate market clearing approximation;
- веса robust wind posterior и risk-adjusted ranking.

## Что ещё требует калибровки

- коэффициенты потерь по глубине и connection point;
- elasticity по типам потребителей;
- параметры конкретных ВЭС по историческим данным игры;
- GP fallback / balancing tariffs, если правила будут уточнены численно.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
export FLASK_APP=ies_bot_skeleton.web.app:create_app
flask db upgrade -d ies_bot_skeleton/web/migrations
flask seed
flask run
```

Открыть `http://127.0.0.1:5000`

## Полезные страницы

- `/dashboard`
- `/sessions/<id>`
- `/lots/<id>`
- `/lots/item/<lot_id>`
- `/system/<id>`
- `/forecast/<id>`
- `/api/sessions/<id>/post-auction-plan`
- `/api/sessions/<id>/post-auction-plan.yaml`

## Тестирование

Быстрая проверка синтаксиса:

```bash
.venv/bin/python -m compileall -q ies_bot_skeleton tests
```

Целевой 2026-набор:

```bash
.venv/bin/pytest -q tests/test_ies2026_domain.py tests/test_ies2026_web.py tests/test_web_scoring.py
```

## Документация

- [docs/data_schema.md](docs/data_schema.md)
- [docs/architecture/web_first.md](docs/architecture/web_first.md)
- [docs/post_auction_system_template.md](docs/post_auction_system_template.md)
