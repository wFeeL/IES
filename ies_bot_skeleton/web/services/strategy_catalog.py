from __future__ import annotations

from typing import Dict, List

from .ui_text import STRATEGY_LABELS

UNIFIED_ANALYSIS_META: Dict[str, str] = {
    "label": STRATEGY_LABELS["unified"],
    "summary": "Цена лота считается в одном режиме: worst/base/best сценарии, риск-премия, резервный буфер, синергия портфеля и системные ограничения собираются в единый decision-passport.",
    "when_to_use": "Дополнительные стратегии отключены. Сравнивайте лоты по рабочей цене, предельной цене, рискам, worst/base/best прибыли и влиянию на портфель в одной сессии.",
}


def strategy_meta(code: str | None) -> Dict[str, str]:
    del code
    return dict(UNIFIED_ANALYSIS_META)


def strategy_list() -> List[Dict[str, str]]:
    return [{"code": "unified", **UNIFIED_ANALYSIS_META}]
