from __future__ import annotations

from typing import Any, Dict, Mapping

from ..models import GameSession, Lot
from .ui_text import stale_reason_label


def analytics_by_lot_for_session(session: GameSession) -> Dict[int, Dict[str, Any]]:
    if not session.lots:
        return {}
    from ...application.analysis import rank_session_lots

    ranking = rank_session_lots(session=session, lots=session.lots, persist=False)
    return {int(row["lot_id"]): dict(row or {}) for row in ranking}


def lot_summary(lot: Lot) -> Dict[str, Any]:
    counts = {"consumer": 0, "generator": 0, "storage": 0, "infrastructure": 0}
    items_total = 0
    names: list[str] = []
    for item in lot.items:
        qty = max(1, int(item.quantity or 1))
        items_total += qty
        category = (item.object_type.category if item.object_type else "other") or "other"
        counts[category] = counts.get(category, 0) + qty
        if item.object_type is not None:
            names.append(f"{item.object_type.name} x{qty}")
    if counts.get("infrastructure", 0) > 0 and counts.get("generator", 0) > 0 and counts.get("consumer", 0) > 0:
        composition = "mixed"
    elif counts.get("generator", 0) > 0 and counts.get("consumer", 0) > 0:
        composition = "mixed"
    elif counts.get("generator", 0) > 0:
        composition = "generator"
    elif counts.get("consumer", 0) > 0:
        composition = "consumer"
    elif counts.get("storage", 0) > 0:
        composition = "storage"
    elif counts.get("infrastructure", 0) > 0:
        composition = "infrastructure"
    else:
        composition = "all"
    compatibility = "Состав требует ручной сетевой проверки."
    if composition == "mixed":
        compatibility = "Смешанный лот: генерация и потребление в одном составе."
    elif composition == "generator":
        compatibility = "Преимущественно генераторный лот."
    elif composition == "consumer":
        compatibility = "Преимущественно потребительский лот."
    elif composition == "infrastructure":
        compatibility = "Инфраструктурный лот, влияние зависит от текущего портфеля."
    return {
        "items_total": items_total,
        "counts": counts,
        "compatibility": compatibility,
        "composition": composition,
        "composition_label": {
            "all": "Все",
            "consumer": "Потребительский",
            "generator": "Генераторный",
            "mixed": "Смешанный",
            "infrastructure": "Инфраструктурный",
            "storage": "Накопительный",
        }.get(composition, "Смешанный"),
        "structure": ", ".join(names[:4]) if names else "Пустой лот",
    }


def lot_row(lot: Lot, evaluation: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    financial = dict(evaluation.get("financial_breakdown") or {})
    result = dict(financial.get("result") or {})
    losses = dict(financial.get("losses_and_risks") or {})
    stale_reason_raw = str(evaluation.get("stale_reason") or "")
    return {
        "lot": lot,
        "lot_id": int(lot.id),
        "name": lot.name,
        "structure": summary["structure"],
        "composition": summary["composition"],
        "composition_label": summary["composition_label"],
        "summary": summary,
        "evaluation": evaluation,
        "price": float(lot.purchase_price if lot.status == "bought" and lot.purchase_price is not None else lot.current_bid or 0.0),
        "utility": float(evaluation.get("summary_score", 0.0) or 0.0),
        "net_profit": float(result.get("net_profit", 0.0) or 0.0),
        "risk": float(losses.get("risk_total", 0.0) or 0.0),
        "max_bid": float((evaluation.get("decision_summary") or {}).get("hard_bid", 0.0) or 0.0),
        "status": lot.status,
        "is_stale": bool(evaluation.get("is_stale")),
        "stale_reason": stale_reason_raw,
        "stale_reason_label": stale_reason_label(stale_reason_raw),
    }


def lot_rows_for_session(
    session: GameSession,
    *,
    ranking_map: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[Dict[str, Any]]:
    ranking_map = ranking_map or analytics_by_lot_for_session(session)
    return [lot_row(lot, dict(ranking_map.get(int(lot.id), {}) or {}), lot_summary(lot)) for lot in session.lots]


def _to_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def filter_lot_rows(rows: list[Dict[str, Any]], filters: Mapping[str, Any]) -> list[Dict[str, Any]]:
    status_filter = str(filters.get("status", "all") or "all")
    composition_filter = str(filters.get("composition", "all") or "all")
    price_min = _to_float(filters.get("price_min"))
    price_max = _to_float(filters.get("price_max"))
    utility_min = _to_float(filters.get("utility_min"))
    utility_max = _to_float(filters.get("utility_max"))
    risk_max = _to_float(filters.get("risk_max"))

    out = rows
    if status_filter != "all":
        out = [row for row in out if row["status"] == status_filter]
    if composition_filter != "all":
        out = [row for row in out if row["composition"] == composition_filter]
    if price_min is not None:
        out = [row for row in out if row["price"] >= price_min]
    if price_max is not None:
        out = [row for row in out if row["price"] <= price_max]
    if utility_min is not None:
        out = [row for row in out if row["utility"] >= utility_min]
    if utility_max is not None:
        out = [row for row in out if row["utility"] <= utility_max]
    if risk_max is not None:
        out = [row for row in out if row["risk"] <= risk_max]
    return out


def sort_lot_rows(rows: list[Dict[str, Any]], sort_key: str) -> list[Dict[str, Any]]:
    sort_key = str(sort_key or "utility_desc")
    if sort_key == "profit_desc":
        return sorted(rows, key=lambda row: row["net_profit"], reverse=True)
    if sort_key == "risk_asc":
        return sorted(rows, key=lambda row: row["risk"])
    if sort_key == "bid_desc":
        return sorted(rows, key=lambda row: row["max_bid"], reverse=True)
    if sort_key == "price_asc":
        return sorted(rows, key=lambda row: row["price"])
    if sort_key == "price_desc":
        return sorted(rows, key=lambda row: row["price"], reverse=True)
    return sorted(rows, key=lambda row: row["utility"], reverse=True)
