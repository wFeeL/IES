from __future__ import annotations

from typing import Dict, List

from .ui_text import STRATEGY_LABELS

STRATEGY_CATALOG: Dict[str, Dict[str, str]] = {
    "generation": {
        "label": STRATEGY_LABELS["generation"],
        "summary": "Сильнее ценит выработку и баланс, подходит для портфелей с генераторами.",
        "when_to_use": "Используйте, когда основная ценность лота связана с выработкой и покрытием спроса.",
    },
    "consumer": {
        "label": STRATEGY_LABELS["consumer"],
        "summary": "Повышает приоритет экономики и стабильного питания нагрузки.",
        "when_to_use": "Подходит для лотов, где критична предсказуемость потребления и штрафы за недоотпуск.",
    },
    "balanced": {
        "label": STRATEGY_LABELS["balanced"],
        "summary": "Нейтральный профиль для смешанных лотов без выраженного перекоса.",
        "when_to_use": "Используйте как базовый вариант, если нет доминирующего приоритета по риску, экологии или генерации.",
    },
    "storage": {
        "label": STRATEGY_LABELS["storage"],
        "summary": "Делает акцент на гибкости и управляемости накопителей.",
        "when_to_use": "Подходит, когда накопитель используется как основной стабилизатор результата и маневренности.",
    },
    "eco": {
        "label": STRATEGY_LABELS["eco"],
        "summary": "Повышает вес зелёной составляющей при сохранении экономической адекватности.",
        "when_to_use": "Используйте, если экологические очки и возобновляемая генерация важны для итоговой стратегии.",
    },
    "risk_averse": {
        "label": STRATEGY_LABELS["risk_averse"],
        "summary": "Сильнее штрафует риск и нестабильность, подходит при высокой неопределённости.",
        "when_to_use": "Подходит для осторожной торговли, когда важнее избежать просадки, чем максимально разогнать ожидаемую ценность.",
    },
    "aggressive": {
        "label": STRATEGY_LABELS["aggressive"],
        "summary": "Максимально смещает оценку в сторону экономики и допускает больший риск.",
        "when_to_use": "Используйте, когда хотите бороться за верхнюю доходность и готовы принять более волатильный результат.",
    },
}


def strategy_meta(code: str | None) -> Dict[str, str]:
    fallback = {
        "label": code or "Не выбрана",
        "summary": "Описание стратегии недоступно.",
        "when_to_use": "Проверьте настройки ruleset или выберите одну из стандартных стратегий.",
    }
    if not code:
        return fallback
    return dict(STRATEGY_CATALOG.get(code, fallback))


def strategy_list() -> List[Dict[str, str]]:
    return [{"code": code, **meta} for code, meta in STRATEGY_CATALOG.items()]
