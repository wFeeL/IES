from __future__ import annotations

from typing import Dict, List

from .ui_text import STRATEGY_LABELS

DEFAULT_STRATEGY_CODE = "unified"
STRATEGY_ORDER = [
    "unified",
    "balanced",
    "generation",
    "consumer",
    "storage",
    "eco",
    "risk_averse",
    "aggressive",
]
STRATEGY_META: Dict[str, Dict[str, str]] = {
    "unified": {
        "label": STRATEGY_LABELS["unified"],
        "summary": "Один основной режим ИЭС 2026: unified lot optimizer ранжирует лоты и комбинации по risk-adjusted profit.",
        "when_to_use": "Это основной пользовательский режим; переключение профилей стратегии в рабочем потоке больше не требуется.",
    },
    "balanced": {
        "label": STRATEGY_LABELS["balanced"],
        "summary": "Legacy-ярлык для обратной совместимости. В UI он схлопывается в единый оптимизатор 2026.",
        "when_to_use": "Используется только при чтении старых payload/import данных.",
    },
    "generation": {
        "label": STRATEGY_LABELS["generation"],
        "summary": "Приоритизирует генерацию, полезную энергию на границе сети и market upside при допустимых потерях.",
        "when_to_use": "Подходит, когда нужно наращивать экспорт и закрывать дефицит собственного supply.",
    },
    "consumer": {
        "label": STRATEGY_LABELS["consumer"],
        "summary": "Ставит выше fixed-connection cashflow и платёжеспособные нагрузки, но не игнорирует цену энергии и topology risk.",
        "when_to_use": "Полезно, если генерация уже есть и нужно расширять потребительскую базу без раздувания потерь.",
    },
    "storage": {
        "label": STRATEGY_LABELS["storage"],
        "summary": "Усиливает ценность накопителей, anti-dumping support, balancing relief и резервную гибкость.",
        "when_to_use": "Применяйте, когда в системе много профицитных/дефицитных окон и важна гибкость по тактам.",
    },
    "eco": {
        "label": STRATEGY_LABELS["eco"],
        "summary": "Отдаёт приоритет ВИЭ и решениям с меньшими потерями и более чистым балансом.",
        "when_to_use": "Подходит, если генерация из солнца/ветра и аккуратная топология важнее агрессивного тарифного роста.",
    },
    "risk_averse": {
        "label": STRATEGY_LABELS["risk_averse"],
        "summary": "Жёстче штрафует topology, market и balancing risks, удерживая запас под второй круг и ремонт схемы.",
        "when_to_use": "Используйте при нестабильном прогнозе, слабой сети или дорогих ошибках монтажа.",
    },
    "aggressive": {
        "label": STRATEGY_LABELS["aggressive"],
        "summary": "Смещает акцент в сторону быстрого захвата положительного delta-profit и допускает более острые bid ceilings.",
        "when_to_use": "Подходит, когда нужно атаковать сильные лоты и команда готова терпеть больший сценарный разброс.",
    },
}


def normalize_strategy_code(code: str | None) -> str:
    raw = str(code or "").strip().lower()
    if raw in STRATEGY_META:
        return raw
    return DEFAULT_STRATEGY_CODE


def strategy_meta(code: str | None) -> Dict[str, str]:
    selected = normalize_strategy_code(code)
    return {"code": selected, **dict(STRATEGY_META[selected])}


def strategy_list() -> List[Dict[str, str]]:
    return [{"code": code, **dict(STRATEGY_META[code])} for code in STRATEGY_ORDER if code in STRATEGY_META]


__all__ = [
    "DEFAULT_STRATEGY_CODE",
    "STRATEGY_META",
    "STRATEGY_ORDER",
    "normalize_strategy_code",
    "strategy_list",
    "strategy_meta",
]
