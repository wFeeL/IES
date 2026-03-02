# Lot Valuation Tool (MVP)

## Быстрый старт

```bash
# из корня проекта
python -m ies_bot_skeleton.cli lottool eval --lot ies_bot_skeleton/lot_tool/data/lots/L12.json
python -m ies_bot_skeleton.cli lottool rank
python -m ies_bot_skeleton.cli lottool suggest-bid --lot ies_bot_skeleton/lot_tool/data/lots/L12.json --pwin 0.35
```

Прогнозы CSV (опционально) положите в `lot_tool/data/forecasts/`.

Legacy-режим:
```bash
# если нужен прямой вызов lottool без ies
PYTHONPATH=ies_bot_skeleton/lot_tool/src python -m lottool.cli --help
```
