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

STALE_REASON_LABELS: Dict[str, str] = {
    "forecast_changed": "изменился активный прогноз",
    "lot_changed": "изменились параметры лота",
    "portfolio_changed": "изменился портфель сессии",
    "object_changed": "изменилась энергосистема",
    "ruleset_changed": "изменились правила расчёта",
    "object_type_changed": "обновлены типы объектов",
}


def strategy_label(code: str | None) -> str:
    if not code:
        return "Не выбрана"
    return STRATEGY_LABELS.get(code, code)


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


def stale_reason_label(raw_reason: str | None) -> str:
    if not raw_reason:
        return ""
    parts = [part.strip() for part in str(raw_reason).split(";") if part.strip()]
    if not parts:
        return ""
    translated: list[str] = []
    for part in parts:
        translated.append(STALE_REASON_LABELS.get(part, part.replace("_", " ")))
    uniq: list[str] = []
    for item in translated:
        if item not in uniq:
            uniq.append(item)
    return "; ".join(uniq)
