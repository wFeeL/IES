from __future__ import annotations

from typing import Dict, List

DEFAULT_STRATEGY_CODE = "unified"
STRATEGY_ORDER = ["unified"]
STRATEGY_META: Dict[str, Dict[str, str]] = {
    "unified": {
        "label": "Unified optimizer 2026",
        "summary": "Единый боевой режим ИЭС 2026: ранжирование лотов и комбинаций по risk-adjusted profit без ручного выбора стратегии.",
        "when_to_use": "Используется всегда. Пользовательский selector стратегий удалён из продукта.",
    }
}


def normalize_strategy_code(code: str | None) -> str:
    return DEFAULT_STRATEGY_CODE


def strategy_meta(code: str | None) -> Dict[str, str]:
    return {"code": DEFAULT_STRATEGY_CODE, **dict(STRATEGY_META[DEFAULT_STRATEGY_CODE])}


def strategy_list() -> List[Dict[str, str]]:
    return [{"code": DEFAULT_STRATEGY_CODE, **dict(STRATEGY_META[DEFAULT_STRATEGY_CODE])}]


__all__ = [
    "DEFAULT_STRATEGY_CODE",
    "STRATEGY_META",
    "STRATEGY_ORDER",
    "normalize_strategy_code",
    "strategy_list",
    "strategy_meta",
]
