from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, cast

from ..models import GameSession, Lot, LotItem
from .ui_text import stale_reason_label, working_bid_reason_short


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _lot_id(raw: Any) -> int:
    return int(raw or 0)


def _lot_items(lot: Lot) -> Sequence[LotItem]:
    return cast(Sequence[LotItem], list(lot.items))


def analytics_by_lot_for_session(session: GameSession) -> Dict[int, Dict[str, Any]]:
    lots = _session_lots(session)
    if not lots:
        return {}
    from ...application.analysis import rank_session_lots

    ranking = rank_session_lots(session=session, lots=lots, persist=False)
    return {_lot_id(row.get("lot_id")): dict(row or {}) for row in ranking}


def lot_summary(lot: Lot) -> Dict[str, Any]:
    counts = {"consumer": 0, "generator": 0, "storage": 0, "infrastructure": 0}
    items_total = 0
    structure_items: list[Dict[str, Any]] = []
    for item in _lot_items(lot):
        qty = max(1, int(item.quantity or 1))
        items_total += qty
        category = (item.object_type.category if item.object_type else "other") or "other"
        counts[category] = counts.get(category, 0) + qty
        object_name = item.object_type.name if item.object_type is not None else "Неизвестный тип"
        object_code = (
            item.object_type.code if item.object_type is not None else str(item.object_type_id)
        )
        structure_items.append(
            {
                "object_type_id": int(item.object_type_id),
                "code": object_code,
                "name": object_name,
                "quantity": qty,
                "label": f"{object_name} ×{qty}",
            }
        )
    if (
        counts.get("infrastructure", 0) > 0
        and counts.get("generator", 0) > 0
        and counts.get("consumer", 0) > 0
    ):
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
        "structure": (
            ", ".join(item["label"] for item in structure_items)
            if structure_items
            else "Пустой лот"
        ),
        "structure_items": structure_items,
    }


def lot_row(lot: Lot, evaluation: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    financial = dict(evaluation.get("financial_breakdown") or {})
    result = dict(financial.get("result") or {})
    losses = dict(financial.get("losses_and_risks") or {})
    decision_summary = dict(evaluation.get("decision_summary") or {})
    recommended_bid_safe = float(
        decision_summary.get("recommended_bid_safe", decision_summary.get("cautious_bid", 0.0))
        or 0.0
    )
    recommended_bid_balanced = float(
        decision_summary.get("recommended_bid_balanced", decision_summary.get("target_bid", 0.0))
        or 0.0
    )
    recommended_bid_aggressive = float(
        decision_summary.get("recommended_bid_aggressive", decision_summary.get("target_bid", 0.0))
        or 0.0
    )
    working_bid = float(
        evaluation.get("working_bid")
        or decision_summary.get("working_bid")
        or recommended_bid_balanced
        or 0.0
    )
    recommended_bid = float(
        decision_summary.get("recommended_bid", working_bid)
        or recommended_bid_balanced
        or working_bid
    )
    max_bid = float(
        decision_summary.get(
            "max_bid",
            min(
                float(decision_summary.get("hard_ceiling_bid", 0.0) or 0.0),
                float(decision_summary.get("budget_remaining", 0.0) or 0.0),
            ),
        )
        or 0.0
    )
    system_check = dict(evaluation.get("system_check") or {})
    zero_bid_reason = str(
        decision_summary.get("zero_bid_reason") or evaluation.get("zero_bid_reason") or ""
    )
    cap_reason = str(
        decision_summary.get("cap_reason")
        or evaluation.get("cap_reason")
        or decision_summary.get("max_bid_reason")
        or ""
    )
    bid_constraints_summary = str(
        decision_summary.get("bid_constraints_summary")
        or ((evaluation.get("metrics") or {}).get("bids") or {}).get("bid_constraints_summary")
        or ""
    )
    working_bid_reason = str(
        evaluation.get("working_bid_reason") or decision_summary.get("working_bid_reason") or ""
    )
    if zero_bid_reason:
        decision_status = "pass"
        decision_status_label = "Пас"
        decision_reason = zero_bid_reason
    elif recommended_bid <= max(0.0, max_bid * 0.35):
        decision_status = "cheap_only"
        decision_status_label = "Только дёшево"
        decision_reason = working_bid_reason or bid_constraints_summary or cap_reason
    else:
        decision_status = "go"
        decision_status_label = "Брать"
        decision_reason = working_bid_reason or bid_constraints_summary or cap_reason
    recommended_points = [
        str(point).strip()
        for point in list(system_check.get("recommended_points") or [])
        if str(point).strip()
    ]
    stale_reason_raw = str(evaluation.get("stale_reason") or "")
    return {
        "lot": lot,
        "lot_id": int(lot.id),
        "name": lot.name,
        "structure": summary["structure"],
        "structure_items": list(summary.get("structure_items") or []),
        "composition": summary["composition"],
        "composition_label": summary["composition_label"],
        "summary": summary,
        "evaluation": evaluation,
        "price": float(
            lot.purchase_price
            if lot.status == "bought" and lot.purchase_price is not None
            else lot.current_bid or 0.0
        ),
        "utility": float(evaluation.get("summary_score", 0.0) or 0.0),
        "net_profit": float(result.get("net_profit", 0.0) or 0.0),
        "gross_profit_before_bid": float(
            decision_summary.get(
                "gross_expected_profit_before_bid",
                result.get("gross_profit_before_bid", 0.0),
            )
            or 0.0
        ),
        "net_profit_at_recommended_bid": float(
            decision_summary.get(
                "net_profit_at_recommended_bid",
                result.get("net_profit_at_recommended_bid", 0.0),
            )
            or 0.0
        ),
        "net_profit_at_max_bid": float(
            decision_summary.get(
                "net_profit_at_max_bid",
                result.get("net_profit_at_max_bid", 0.0),
            )
            or 0.0
        ),
        "remaining_budget_after_recommended_bid": float(
            decision_summary.get(
                "remaining_budget_after_recommended_bid",
                result.get("remaining_budget_after_recommended_bid", 0.0),
            )
            or 0.0
        ),
        "remaining_budget_after_max_bid": float(
            decision_summary.get(
                "remaining_budget_after_max_bid",
                result.get("remaining_budget_after_max_bid", 0.0),
            )
            or 0.0
        ),
        "risk": float(losses.get("risk_total", 0.0) or 0.0),
        "working_bid": float(working_bid),
        "recommended_bid_safe": float(recommended_bid_safe),
        "recommended_bid_balanced": float(recommended_bid_balanced),
        "recommended_bid_aggressive": float(recommended_bid_aggressive),
        "recommended_bid": float(recommended_bid),
        "hard_ceiling_bid": float(decision_summary.get("hard_ceiling_bid", 0.0) or 0.0),
        "max_bid": float(max_bid),
        "working_bid_source": str(
            evaluation.get("working_bid_source")
            or decision_summary.get("working_bid_source")
            or "none"
        ),
        "working_bid_reason": str(working_bid_reason),
        "working_bid_short_reason": working_bid_reason_short(working_bid_reason),
        "target_bid": float(decision_summary.get("target_bid", recommended_bid_balanced) or 0.0),
        "cautious_bid": float(decision_summary.get("cautious_bid", recommended_bid_safe) or 0.0),
        "p_win": float(decision_summary.get("p_win", 0.0) or 0.0),
        "serious_competitors": int(decision_summary.get("serious_competitors", 0) or 0),
        "budget_adjusted_bid": float(decision_summary.get("budget_adjusted_bid", 0.0) or 0.0),
        "recommended_bid_reason": str(
            decision_summary.get("recommended_bid_reason")
            or decision_summary.get("working_bid_reason")
            or evaluation.get("working_bid_reason")
            or ""
        ),
        "zero_bid_reason": str(zero_bid_reason),
        "cap_reason": str(cap_reason),
        "bid_constraints_summary": str(bid_constraints_summary),
        "cap_bindings": list(decision_summary.get("cap_bindings") or []),
        "decision_status": str(decision_status),
        "decision_status_label": str(decision_status_label),
        "decision_reason": str(decision_reason),
        "decision_reason_short": working_bid_reason_short(decision_reason),
        "decision_price_range": (
            f"безопасная {recommended_bid_safe:.1f} · целевая {recommended_bid_balanced:.1f} · потолок {max_bid:.1f}"
            if recommended_bid_balanced > 0.0
            else "безопасная 0 · целевая 0 · потолок 0"
        ),
        "budget_preservation_note": str(decision_summary.get("budget_preservation_note") or ""),
        "max_bid_reason": str(decision_summary.get("max_bid_reason") or ""),
        "system_check": system_check,
        "connection_fit_status": str(system_check.get("status") or "neutral"),
        "recommended_points": recommended_points,
        "connection_block_reasons_count": int(
            system_check.get("connection_block_reasons_count", 0) or 0
        ),
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
    return [
        lot_row(lot, dict(ranking_map.get(int(lot.id), {}) or {}), lot_summary(lot))
        for lot in _session_lots(session)
    ]


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
        return sorted(
            rows,
            key=lambda row: float(
                row.get("recommended_bid") or row.get("working_bid") or row.get("target_bid") or 0.0
            ),
            reverse=True,
        )
    if sort_key == "price_asc":
        return sorted(rows, key=lambda row: row["price"])
    if sort_key == "price_desc":
        return sorted(rows, key=lambda row: row["price"], reverse=True)
    return sorted(rows, key=lambda row: row["utility"], reverse=True)
