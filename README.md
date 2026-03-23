# IES Web 2026

Веб-инструмент для моделирования сессии ИЭС по правилам 2026 года: объекты, аукцион, схема сети, прогнозы, оценка лотов и пользовательская аналитика.

## Что изменилось

Проект переведён с упрощённой логики 2024/25 на доменную модель ИЭС 2026:

- базовый расчёт лота теперь строится через `expected_delta_profit`, а не через абстрактную utility-оценку;
- основной предмет аукциона: тариф подключения/обслуживания за такт;
- потребители оцениваются как торги на понижение тарифа;
- генерация, накопители и инфраструктура оцениваются как торги на повышение сервисного тарифа;
- сеть валидируется как дерево без циклов и островов;
- прогноз работает на горизонте `48` тактов;
- учитываются освещённость, несколько wind channels, нагрузка по типам потребителей, рынок, потери и небаланс;
- all-pay больше не является базовой моделью оценки.

## Архитектура

- `ies_bot_skeleton/domain/ies2026/types.py`
  typed-сущности 2026: `EnergyObject`, `ForecastTick`, `LotEvaluation`, `MarketBid`, `NetworkValidationReport`
- `ies_bot_skeleton/domain/ies2026/forecast.py`
  сборка 48-тактного прогноза, load aliases, per-turbine wind channels
- `ies_bot_skeleton/domain/ies2026/network.py`
  планирование и валидация дерева, dual-input hospital/factory, энергорайоны, параметризуемые сетевые потери
- `ies_bot_skeleton/domain/ies2026/engine.py`
  marginal value / delta-profit engine, market bids, anti-dumping ramp, balancing penalties, storage value, enabler value
- `ies_bot_skeleton/web/services/evaluation.py`
  web-compatible wrapper над новым движком
- `ies_bot_skeleton/web/services/network.py`
  web validator / presenter для topology issues
- `ies_bot_skeleton/web/templates/`
  обновлённый SSR-интерфейс: overview, лоты, lot detail, сеть, прогноз

## Ключевые правила 2026, заложенные в код

- объекты:
  `main_substation`, `mini_substation`, `solar`, `wind`, `storage`, `house_a`, `house_b`, `office`, `factory`, `hospital`
- сеть:
  - один корень: главная подстанция
  - нет циклов
  - нет островов
  - у каждого активного объекта должен быть путь до главной подстанции
  - генерация и потребители не смешиваются в одном энергорайоне
  - больница требует два ввода
  - завод допускает один или два ввода; один ввод даёт warning, не critical
- прогноз:
  - 48 тактов
  - `illumination`
  - несколько `wind_*` каналов
  - потребление по типам
  - market buy / sell / balancing penalty
- накопитель:
  - ёмкость `120 МВт*такт`
  - заряд до `15 МВт*такт`
  - разряд до `20 МВт*такт`
  - оценка разделена на `arbitrage / balancing / reserve`
- рынок:
  - отдельная сущность заявленного объёма продажи
  - low-price sale для непроданного остатка
  - balancing penalty за недопоставку
  - anti-dumping ramp на рост продаваемого объёма

## Формат оценки лота

Каждый лот получает:

- `expected_delta_profit`
- `break_even_tariff`
- `recommended_bid_or_tariff`
- `best_case`
- `base_case`
- `worst_case`
- `topology_risk`
- `market_risk`
- `balancing_risk`
- `loss_risk`
- `explanation`

Дополнительно в breakdown есть:

- доход от потребителей
- доход/расход по рынку
- сервисные расходы
- потери сети
- небаланс
- вклад накопителей
- marginal vs standalone contribution
- enabler value для инфраструктуры

## Допущения модели

Модель явно помечает места, где нет точной игровой формулы:

- эластичность потребителей задана параметризуемой `demand elasticity model`;
- потери сети считаются параметризуемой аппроксимацией по глубине дерева, точке подключения и пересечению энергорайона;
- ВЭС использует параметризуемую power curve (`cut-in / rated / cut-out`);
- рыночный экспорт моделируется через conservative declared sale + ramp limit;
- compatibility aliases для старых кодов (`house`, `mini_substation_a`, `mini_substation_b`, `cyber_solar`, `tps`) канонизируются в 2026-типы и скрыты из основного UI.

## Что ещё требует калибровки

- коэффициенты потерь по точкам подключения и глубине;
- demand elasticity по реальным данным игры;
- параметры wind curves для конкретных ВЭС;
- low-price sale factor и balancing penalty, если правила будут уточнены;
- enabler value инфраструктуры при сложных каскадных комбинациях.

## Запуск

### 1. Установить зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2. Поднять БД и seed

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
flask db upgrade -d ies_bot_skeleton/web/migrations
flask seed
```

### 3. Запустить сервер

```bash
export FLASK_APP=ies_bot_skeleton.web.app:create_app
export IES_WEB_ENV=development
flask run
```

Открыть `http://127.0.0.1:5000`

После `seed` доступны:

- `admin / admin123`
- `analyst / analyst123`

## Базовый пользовательский сценарий

1. Создать сессию.
2. Проверить раздел `Объекты` и убедиться, что есть главная подстанция и корректная схема.
3. Загрузить прогноз в разделе `Прогнозы`.
4. Убедиться, что прогноз совместим с типами объектов.
5. Перейти в `Аукцион и лоты`.
6. Открыть карточку лота и сравнить:
   - `recommended tariff`
   - `break-even`
   - `expected delta-profit`
   - `best/base/worst`
   - `topology / market / balancing / loss risk`
   - монтажные требования
7. При необходимости исправить схему в разделе `Схема сети`.
8. Подтвердить покупку лота по выбранному тарифу.

## Основные страницы

- `/dashboard` — список сессий
- `/sessions/<id>` — overview сессии
- `/system/<id>` — схема сети и валидация 2026
- `/forecast/<id>` — прогнозы и weather/market analysis
- `/lots/<id>` — список лотов и consolidated analysis
- `/lots/item/<lot_id>` — карточка лота
- `/catalog` — справочник и правила

## Разработка

### Быстрый smoke-check

```bash
.venv/bin/python -m compileall -q ies_bot_skeleton tests
```

### Целевой 2026-набор тестов

```bash
.venv/bin/pytest -q tests/test_ies2026_domain.py tests/test_ies2026_web.py
```

Проверено в репозитории:

- `9 passed`

### Полный цикл

```bash
source .venv/bin/activate
flask --app ies_bot_skeleton.web.app:create_app run
```

## Документация

- [docs/data_schema.md](docs/data_schema.md)
- [docs/architecture/web_first.md](docs/architecture/web_first.md)
