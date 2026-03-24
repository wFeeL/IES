from __future__ import annotations

from typing import Dict

SESSION_TERMS = {
    "collection_title": "Список сессий",
    "create_title": "Создать сессию",
    "open_cta": "Открыть сессию",
    "delete_cta": "Удалить сессию",
}

STRATEGY_LABELS: Dict[str, str] = {
    "unified": "Единый оптимизатор",
    "generation": "Фокус на генерации",
    "consumer": "Фокус на потребителях",
    "balanced": "Сбалансированная стратегия",
    "storage": "Фокус на накопителях",
    "eco": "Эко-стратегия",
    "risk_averse": "Осторожная стратегия",
    "aggressive": "Агрессивная стратегия",
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

FORECAST_FACTOR_LABELS: Dict[str, str] = {
    "wind_factor": "Фактор ветра",
    "solar_factor": "Фактор солнца",
    "market_price_buy": "Рыночная цена покупки",
    "market_price_sell": "Рыночная цена продажи",
    "balancing_penalty_price": "Штраф за небаланс",
    "fuel_price": "Цена топлива",
    "temperature": "Температура",
    "time_of_day": "Время суток",
}

FORECAST_PROFILE_LABELS: Dict[str, str] = {
    "factory_load": "Нагрузка завода",
    "office_load": "Нагрузка офиса",
    "house_a_load": "Нагрузка домов A",
    "house_b_load": "Нагрузка домов B",
    "hospital_load": "Нагрузка больницы",
    "storage_default_profile": "Профиль накопителя",
}

FORECAST_LOAD_LABELS: Dict[str, str] = {
    "house_a": "Дома типа A",
    "house_b": "Дома типа B",
    "office": "Офисная нагрузка",
    "factory": "Промышленная нагрузка",
    "hospital": "Нагрузка больницы",
    "consumer": "Общая потребительская нагрузка",
    "load": "Совокупная нагрузка",
    "class3": "Служебный ряд: class3",
}

FORECAST_SERVICE_LOAD_KEYS = {"class3", "consumer", "load"}


def strategy_label(code: str | None) -> str:
    del code
    return "Единый оптимизатор"


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


def forecast_series_label(key: str | None) -> str:
    if not key:
        return "—"
    code = str(key).strip().lower()
    if code in FORECAST_FACTOR_LABELS:
        return FORECAST_FACTOR_LABELS[code]
    if code in FORECAST_PROFILE_LABELS:
        return FORECAST_PROFILE_LABELS[code]
    if code in FORECAST_LOAD_LABELS:
        return FORECAST_LOAD_LABELS[code]
    return code.replace("_", " ")


def working_bid_reason_short(reason: str | None) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "balanced bid" in lowered and "ограничена бюджетом" in lowered:
        return "Balanced ограничена бюджетом"
    if "только по дешёвому входу" in lowered or "можно брать только по дешёвому входу" in lowered:
        return "Только дёшево"
    if "safe bid" in lowered:
        return "Safe режим"
    if "pwin-aware" in lowered:
        return "Pwin-aware модель"
    if "консервативная полезность неположительная" in lowered:
        return "Нет консервативной полезности"
    if "ограничена бюджетом" in lowered:
        return "Ограничено бюджетом"
    if "снижена относительно target" in lowered or "снижена относительно target" in lowered:
        return "Снижен риск-буфером"
    if "совпадает с целевой ставкой" in lowered:
        return "Полная рабочая цена"
    if "бюджет сессии исчерпан" in lowered:
        return "Бюджет исчерпан"
    if "недостаточно бюджета после резервного буфера" in lowered:
        return "Недостаточно бюджета"
    if "экономически оправданного потолка" in lowered:
        return "Цена выше потолка"
    if "текущая цена уже выше" in lowered and "потолка" in lowered:
        return "Цена выше потолка"
    if "отрицательная экономика" in lowered:
        return "Отрицательная экономика"
    if "отрицательная маржинальная экономика" in lowered:
        return "Отрицательная экономика"
    if "структурно не поддерживается системой" in lowered or "критически низкий fit" in lowered:
        return "Лот несовместим"
    if "маржинального anchor value" in lowered:
        return "Рабочая ставка"
    if "ликвидностью после all-pay" in lowered:
        return "Ограничено ликвидностью"
    if "ставка небольшая" in lowered:
        return "Ставка снижена риском"
    if "снижена из-за бюджетного ограничения" in lowered:
        return "Ограничено бюджетом"
    if (
        "weighted expected" in lowered
        or "взвешенная маржинальная прибыль" in lowered
        or "ожидаемая чистая прибыль неположительная" in lowered
    ):
        return "Нет маржинальной прибыли"
    if "осторожная ставка выше доступного остатка бюджета" in lowered:
        return "Текущий бюджет слишком мал"
    if "риск-премия перекрывает экономический эффект" in lowered:
        return "Риск перекрывает эффект"
    if "допустимую ставку" in lowered:
        return "Ставка не формируется"
    if "не формирует оправданную цену входа" in lowered:
        return "Нет оправданной ставки"
    return text
