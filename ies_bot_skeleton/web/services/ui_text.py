from __future__ import annotations

from typing import Dict

SESSION_TERMS = {
    "collection_title": "Список сессий",
    "create_title": "Создать сессию",
    "open_cta": "Открыть сессию",
    "delete_cta": "Удалить сессию",
}

STRATEGY_LABELS: Dict[str, str] = {
    "generation": "Генерация-ориентированная",
    "consumer": "Потребительская",
    "balanced": "Сбалансированная",
    "storage": "Накопительная",
    "eco": "Экологическая",
    "risk_averse": "Риск-консервативная",
    "aggressive": "Агрессивная аукционная",
}

ANALYSIS_MODE_LABELS: Dict[str, str] = {
    "forecast": "С прогнозом",
    "no_forecast": "Без прогноза",
}

CATEGORY_LABELS: Dict[str, str] = {
    "consumer": "Потребители",
    "generator": "Генераторы",
    "storage": "Накопители",
    "infrastructure": "Инфраструктура",
}

LOT_SCOPE_LABELS: Dict[str, str] = {
    "start": "Стартовый",
    "normal": "Обычный",
    "local": "Локальный",
    "global": "Глобальный",
}

LOT_STATUS_LABELS: Dict[str, str] = {
    "available": "Доступен",
    "bought": "Куплен",
    "rejected": "Отклонен",
}


def strategy_label(code: str | None) -> str:
    if not code:
        return "Не выбрана"
    return STRATEGY_LABELS.get(code, code)


def analysis_mode_label(code: str | None) -> str:
    if not code:
        return "Не выбран"
    return ANALYSIS_MODE_LABELS.get(code, code)


def category_label(code: str | None) -> str:
    if not code:
        return "Без категории"
    return CATEGORY_LABELS.get(code, code)


def lot_scope_label(code: str | None) -> str:
    if not code:
        return "—"
    return LOT_SCOPE_LABELS.get(code, code)


def lot_status_label(code: str | None) -> str:
    if not code:
        return "—"
    return LOT_STATUS_LABELS.get(code, code)
