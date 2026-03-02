ИЭС / НТО — каркас управляющего скрипта + инструмент оценки лотов

Единая точка запуска из корня проекта:
- python -m ies_bot_skeleton.cli bot
- python -m ies_bot_skeleton.cli lottool ...
- python -m ies ...  (алиас для совместимости)

1) Управляющий скрипт (симуляция на тактах)
- Точка входа: main.py
- Настройки: constants.py
- Авто-детект команд: adapters.py
- Контроллеры: controllers_*.py

2) Lot Valuation Tool (оценка полезности лота для all-pay аукциона)
- Папка: lot_tool/
- CLI (через единый запуск): python -m ies_bot_skeleton.cli lottool ...
- Legacy-режим (совместимость): PYTHONPATH=ies_bot_skeleton/lot_tool/src python -m lottool.cli ...
- Функции:
  * score(state) -> breakdown
  * marginal_value(state, lot) -> Δbreakdown (base/worst/best)
  * suggest-bid: EV = pwin*v - b

Конфиги:
- lot_tool/config/config_game.json (константы года)
- lot_tool/config/config_team.json (веса/профиль/риск)

CSV прогнозы (опционально):
- wide: tick, key1, key2, ...
- long: tick, id, value
- имена: wind*.csv, solar*.csv, load*.csv
