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
7. Перейти в `Lots`, открыть `Lot Detail` и проверить `Worst / Base / Best`, `system-check`, рекомендуемую ставку и прибыль после неё.
8. Открыть `Quick Auction`, проверить hotkeys `1..9`, `P/W/S/T/M`, `E`, `R`, `B` и потоковые действия без ручного ввода ID.
9. Убедиться, что после покупки сразу обновились бюджет, shortlist и strategy snapshot.

## Актуальное поведение

- Единый формат чисел: целые значения отображаются без `.0` во всех основных экранах (dashboard, lots, lot detail, forecast, quick auction, system).
- Рекомендуемая ставка (`recommended_bid`, она же `working_bid` для backward compatibility) считается как повторяемая аукционная ставка, а не как доля полной value лота:
  - сначала строится `value_anchor` из консервативной полезности, gross-profit и риск-очищенной маржи;
  - затем anchor жёстко режется через `fit_factor`, `risk_factor`, `volatility_factor`, `synergy_factor`, `reserve_factor`, `allpay_factor`, `reserve_budget_factor`, `budget_pressure_factor`;
  - `p_win` и `serious_competitors` участвуют только как мягкий `competition_factor`, а не как главный драйвер ставки;
  - `adjusted_value` становится базой для трёх уровней цены: `safe_bid`, `target_bid`, `recommended_bid_aggressive`;
  - `hard_ceiling_bid`/`max_bid` ограничивается одновременно value, риском и ликвидностью и обязан оставлять крупную долю прибыли в запасе.
- Логика зануления теперь узкая и объяснимая:
  - ставка `0` даётся при отрицательной экономике, перегретой текущей цене, критическом system fit или нехватке бюджета после reserve/all-pay;
  - прибыльный совместимый лот не зануляется без серьёзной причины;
  - если лот годный, но вход возможен только очень дёшево, `working_bid_source` будет `tight_entry`, а не `zero`.
- В `decision_summary` и `metrics.bids` есть explainability-блок: `value_anchor`, `adjusted_value`, `fit_factor`, `risk_factor`, `volatility_factor`, `competition_factor`, `bid_constraints_summary`, `cap_bindings`, `zero_bid_reason`, `cap_reason`.
- UI и API показывают раздельный бюджетный breakdown: `budget_total`, `cash_available`, `reserved_budget`, `purchase_spent`, `allpay_spent`, `spent_total`, `remaining_budget`.
- All-pay работает через event flow:
  - `Bid` создаёт pending-событие;
  - `Lost` уменьшает `cash_available` через `allpay_spent`;
  - `Won` проводит покупку по ставке без двойного all-pay списания.
- Остаток бюджета после покупки не сгорает и считается ресурсом следующих аукционов, поэтому модель не пытается искусственно поднять ставку до всего доступного остатка.
- UI на рабочих экранах упрощён:
  - в таблицах на первом экране остаются название, состав, текущая цена, краткий net/utility, safe-target-cap и короткий статус;
  - подробные числа и объяснения уходят в detail page и в secondary text;
  - короткие статусы сведены к `Брать`, `Только дёшево`, `Пас`.
- `working_bid` не остаётся положительным при неположительной взвешенной маржинальной прибыли: если ставка равна `0`, UI обязан показать честную причину через `working_bid_reason`.
- Оценка лота считается как маржинальный вклад к текущему портфелю на всём горизонте активного прогноза, а не как статическое число из summary.
- После покупки лота рабочие цены и стратегия остальных лотов пересчитываются на новом контексте портфеля.
- Quick auction теперь безопаснее:
  - выбор текущего лота идёт кликом из списка или из ranking table, ручного ввода ID нет;
  - поле цены по умолчанию заполняется текущей рыночной ценой, а не автоматически завышенным working bid;
  - кнопки `Pass`, `Watch`, `Подставить safe`, `Подставить target`, `Подставить max cap` только подсказывают/подставляют цену;
  - покупка требует явного checkbox-confirmation цены сделки;
  - если ставка `0` или лот нужно брать только очень дёшево, quick auction показывает причину прямо в decision panel.
- Покупка лота блокируется, если активный прогноз несовместим, и в API, и в SSR-форме покупки.
- Оценка в quick auction не меняет `current_bid`: рыночная ставка и цена покупки разделены.
- После покупки в quick auction автоматически запускается пересчёт и обновление:
  - бюджета;
  - shortlist;
  - strategy snapshot.
- Для quick flow используется fast scoring, а глубокий пересчёт стратегии запускается отдельно через `Deep snapshot` (кнопка и API `force=1`) с кэшем по fingerprint состояния.
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
- Strategy snapshot честно размечен как `what-if`-справка, а не как точный live-план:
  - быстрый режим отдаёт ranking и лёгкий advisory snapshot без тяжёлого follow-up на каждый просмотр;
  - deep mode включает более дорогой пересчёт и follow-up сценарии;
  - `after_purchase` и `after_loss` в fast mode могут быть скрыты или показаны как placeholder с явным текстом, что нужен deep snapshot.
- Snapshot показывает комбинации в формате `Название (ID) + ...`, per-lot детализацию и сценарные срезы `full_budget`, `after_purchase`, `after_loss`.
- Для каждой комбинации доступны:
  - `<лот> (<id>) — цена: <...>, прибыль: <...>`
- `display_title`, `total_price`, `total_profit`, `synergy`, `utility`, `budget_fit`, `explanation`.
- В сценариях `Worst / Base / Best` используются разные пояснения, зависящие от метрик сценария.
- В `Lot Detail` сценарная карточка показывает value-ставку по сценарию, чистую прибыль после неё и агрессивный потолок отдельно.
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
4. Использовать `Quick Auction`: выбрать лот, посмотреть `current price / safe / target / max cap`, затем явно подтвердить цену сделки.
5. После покупки перейти к новому shortlist и к пересчитанной what-if стратегии на сохранённый остаток бюджета.
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
- `GET /api/sessions/<id>/auction/events`
- `POST /api/sessions/<id>/auction/actions`
- `POST /api/sessions/<id>/auction/outcomes`
- `POST /api/forecast/upload`
- `GET /api/sessions/<id>/forecast-compatibility`

Полный контракт: [docs/data_schema.md](docs/data_schema.md)

## Что считать важным в данных

- `recommended_bid` - основная рабочая ставка для повторяющегося аукциона; она заметно ниже полной экономической value лота.
- `working_bid` - alias `recommended_bid` для совместимости старых потребителей.
- `safe_bid` - консервативная цена, с которой безопасно начинать торг.
- `target_bid` - основная рабочая цена.
- `recommended_bid_aggressive` - агрессивная, но ещё экономически оправданная цена.
- `hard_ceiling_bid` / `max_bid` - жёсткий потолок; выше него UI должен толкать только в сторону `pass`.
- `budget_adjusted_bid` - техническая рабочая ставка после budget/liquidity cap.
- `zero_bid_reason` - явная причина, почему ставка обнулилась.
- `cap_reason` - явная причина, почему потолок именно такой.
- `bid_constraints_summary` - короткая сводка, какие ограничения реально зажали ставку.
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
# advisory: black --check пока не блокирует CI, форматный долг по репозиторию ещё не закрыт полностью
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
