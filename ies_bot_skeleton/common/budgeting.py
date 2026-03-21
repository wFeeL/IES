from __future__ import annotations

from typing import Any, Dict

from ..web.models import GameSession


def start_budget(session: GameSession) -> float:
    return float(session.budget_total or 0.0)


def purchase_spent_total(session: GameSession) -> float:
    spent = 0.0
    for lot in session.lots:
        if str(lot.status or "") != "bought":
            continue
        spent += float(lot.purchase_price or 0.0)
    return float(spent)


def allpay_spent_total(session: GameSession) -> float:
    return max(0.0, float(getattr(session, "allpay_spent", 0.0) or 0.0))


def cash_available(session: GameSession) -> float:
    return max(0.0, start_budget(session) - purchase_spent_total(session) - allpay_spent_total(session))


def reserved_budget(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    reserved_raw = max(0.0, float(reserved_spend))
    return min(cash_available(session), reserved_raw)


def spent_total(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    base_spent = purchase_spent_total(session) + allpay_spent_total(session)
    return float(base_spent + reserved_budget(session, reserved_spend=reserved_spend))


def remaining_budget(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    return max(0.0, cash_available(session) - reserved_budget(session, reserved_spend=reserved_spend))


def budget_snapshot(
    session: GameSession,
    *,
    reserved_spend: float = 0.0,
) -> Dict[str, Any]:
    start = start_budget(session)
    purchase_spent = purchase_spent_total(session)
    allpay_spent = allpay_spent_total(session)
    cash = cash_available(session)
    reserved = reserved_budget(session, reserved_spend=reserved_spend)
    spent = float(purchase_spent + allpay_spent)
    spent_with_reserve = float(spent + reserved)
    return {
        "start_budget": float(start),
        # Backward-compatible alias.
        "budget_total": float(start),
        "purchase_spent": float(purchase_spent),
        "allpay_spent": float(allpay_spent),
        "spent_total": float(spent),
        "spent_total_with_reserve": float(spent_with_reserve),
        "cash_available": float(cash),
        "reserved_budget": float(reserved),
        "remaining_budget": float(max(0.0, cash - reserved)),
    }


__all__ = [
    "allpay_spent_total",
    "budget_snapshot",
    "cash_available",
    "purchase_spent_total",
    "remaining_budget",
    "reserved_budget",
    "spent_total",
    "start_budget",
]
