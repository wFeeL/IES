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


def spent_total(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    return float(
        purchase_spent_total(session)
        + allpay_spent_total(session)
        + max(0.0, float(reserved_spend))
    )


def remaining_budget(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    return max(0.0, start_budget(session) - spent_total(session, reserved_spend=reserved_spend))


def budget_snapshot(
    session: GameSession,
    *,
    reserved_spend: float = 0.0,
) -> Dict[str, Any]:
    start = start_budget(session)
    purchase_spent = purchase_spent_total(session)
    allpay_spent = allpay_spent_total(session)
    spent = spent_total(session, reserved_spend=reserved_spend)
    return {
        "start_budget": float(start),
        # Backward-compatible alias.
        "budget_total": float(start),
        "purchase_spent": float(purchase_spent),
        "allpay_spent": float(allpay_spent),
        "spent_total": float(spent),
        "remaining_budget": float(max(0.0, start - spent)),
    }


__all__ = [
    "allpay_spent_total",
    "budget_snapshot",
    "purchase_spent_total",
    "remaining_budget",
    "spent_total",
    "start_budget",
]
