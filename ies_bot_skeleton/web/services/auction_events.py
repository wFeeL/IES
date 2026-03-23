from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.budgeting import budget_snapshot
from ...application.portfolio import buy_lot
from ..extensions import db
from ..models import AuctionEvent, GameSession, Lot
from .evaluation import evaluate_lot
from .stale import mark_stale_for_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip())


def _allpay_enabled(session: GameSession) -> bool:
    rules_cfg = dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {})
    auction_cfg = dict(rules_cfg.get("auction") or {})
    mode = _norm(auction_cfg.get("allpay_mode", "bid_outcome"))
    return mode not in {"off", "disabled", "none", "false", "0"}


def _allpay_mode(session: GameSession) -> str:
    rules_cfg = dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {})
    auction_cfg = dict(rules_cfg.get("auction") or {})
    return _norm(auction_cfg.get("allpay_mode", "special_only"))


def _allpay_limit(session: GameSession) -> float:
    rules_cfg = dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {})
    auction_cfg = dict(rules_cfg.get("auction") or {})
    return max(0.0, float(auction_cfg.get("allpay_limit", 5000.0) or 5000.0))


def _allpay_remaining(session: GameSession) -> float:
    return max(0.0, _allpay_limit(session) - float(session.allpay_spent or 0.0))


def _special_allpay_mode(auction_mode: Any) -> bool:
    mode = _norm(auction_mode)
    return mode in {"fixed_tariff_package_all_pay", "tie_break_all_pay"}


def _event_allpay_allowed(*, session: GameSession, event: AuctionEvent, lot: Lot) -> bool:
    del lot
    mode = _allpay_mode(session)
    if mode in {"off", "disabled", "none", "false", "0"}:
        return False
    details = dict(event.details_json or {})
    event_mode = details.get("auction_mode")
    allpay_triggered = bool(details.get("allpay_triggered"))
    if mode == "bid_outcome":
        return True
    if mode == "special_only":
        return allpay_triggered and _special_allpay_mode(event_mode)
    return False


def auction_budget_view(session: GameSession, *, reserved_spend: float = 0.0) -> Dict[str, float]:
    snap = budget_snapshot(session, reserved_spend=reserved_spend)
    return {
        "budget_total": float(snap.get("budget_total", 0.0) or 0.0),
        "cash_available": float(snap.get("cash_available", 0.0) or 0.0),
        "reserved_budget": float(snap.get("reserved_budget", 0.0) or 0.0),
        "purchase_spent": float(snap.get("purchase_spent", 0.0) or 0.0),
        "allpay_spent": float(snap.get("allpay_spent", 0.0) or 0.0),
        "allpay_limit": float(_allpay_limit(session)),
        "allpay_remaining": float(_allpay_remaining(session)),
        "spent_total": float(snap.get("spent_total", 0.0) or 0.0),
        "remaining_budget": float(snap.get("remaining_budget", 0.0) or 0.0),
    }


def _event_bid_level_amount(evaluation: Dict[str, Any], bid_level: str) -> float:
    norm = _norm(bid_level)
    summary = dict(evaluation.get("decision_summary") or {})
    if norm == "safe":
        return float(
            evaluation.get("safe_bid")
            or evaluation.get("recommended_bid_safe")
            or summary.get("safe_bid")
            or summary.get("recommended_bid_safe")
            or summary.get("cautious_bid")
            or 0.0
        )
    if norm == "max":
        return float(
            evaluation.get("max_bid")
            or summary.get("max_bid")
            or evaluation.get("hard_cap")
            or summary.get("hard_cap")
            or evaluation.get("hard_ceiling_bid")
            or summary.get("hard_ceiling_bid")
            or 0.0
        )
    return float(
        evaluation.get("target_bid")
        or evaluation.get("recommended_bid")
        or summary.get("target_bid")
        or summary.get("recommended_bid")
        or evaluation.get("working_bid")
        or summary.get("working_bid")
        or 0.0
    )


def _latest_pending_bid_event(*, session_id: int, lot_id: int) -> Optional[AuctionEvent]:
    return (
        db.session.query(AuctionEvent)
        .filter_by(session_id=int(session_id), lot_id=int(lot_id), action="bid", outcome="pending")
        .order_by(AuctionEvent.created_at.desc(), AuctionEvent.id.desc())
        .first()
    )


def list_auction_events(*, session: GameSession, limit: int = 40) -> List[Dict[str, Any]]:
    rows = (
        db.session.query(AuctionEvent)
        .filter_by(session_id=int(session.id))
        .order_by(AuctionEvent.created_at.desc(), AuctionEvent.id.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [row.to_dict() for row in rows]


def apply_auction_action(
    *,
    session: GameSession,
    lot: Lot,
    action: str,
    bid_level: str | None = None,
    bid_amount: float | None = None,
    auction_mode: str | None = None,
    allpay_triggered: bool | None = None,
) -> Dict[str, Any]:
    if int(lot.session_id) != int(session.id):
        raise ValueError("Лот не принадлежит сессии")
    action_norm = _norm(action)
    if action_norm not in {"pass", "watch", "bid"}:
        raise ValueError("Допустимые действия: pass, watch, bid")

    if action_norm == "pass":
        event = AuctionEvent(
            session_id=int(session.id),
            lot_id=int(lot.id),
            action="pass",
            bid_level="",
            amount=0.0,
            outcome="none",
            budget_effect=0.0,
            details_json={
                "lot_status_before": str(lot.status or ""),
                "lot_status_after": str(lot.status or ""),
                "soft_pass": True,
            },
            resolved_at=_utcnow(),
        )
        db.session.add(event)
    elif action_norm == "watch":
        event = AuctionEvent(
            session_id=int(session.id),
            lot_id=int(lot.id),
            action="watch",
            bid_level="",
            amount=0.0,
            outcome="none",
            budget_effect=0.0,
            details_json={"lot_status": str(lot.status or "")},
            resolved_at=None,
        )
        db.session.add(event)
    else:
        if str(lot.status or "") != "available":
            raise ValueError("Ставку можно сделать только на доступный лот")
        bid_level_norm = _norm(bid_level or "target") or "target"
        if bid_level_norm not in {"safe", "target", "max"}:
            raise ValueError("bid_level должен быть safe, target или max")

        if bid_amount is None:
            evaluation = evaluate_lot(session=session, lot=lot, persist=False)
            amount = _event_bid_level_amount(evaluation, bid_level_norm)
        else:
            amount = float(bid_amount or 0.0)

        if amount <= 0.0:
            raise ValueError("Ставка должна быть больше нуля")

        cash_available = float(auction_budget_view(session).get("cash_available", 0.0) or 0.0)
        if amount > cash_available + 1e-9:
            raise ValueError("Ставка превышает доступную ликвидность")
        if bool(allpay_triggered) and _special_allpay_mode(auction_mode) and amount > _allpay_remaining(session) + 1e-9:
            raise ValueError("Ставка превышает остаток специального All-Pay бюджета")

        event = AuctionEvent(
            session_id=int(session.id),
            lot_id=int(lot.id),
            action="bid",
            bid_level=bid_level_norm,
            amount=float(amount),
            outcome="pending",
            budget_effect=0.0,
            details_json={
                "allpay_enabled": _allpay_enabled(session),
                "allpay_mode": _allpay_mode(session),
                "auction_mode": str(auction_mode or "ordinary_tariff_auction"),
                "allpay_triggered": bool(allpay_triggered),
                "allpay_budget_remaining": float(_allpay_remaining(session)),
            },
            resolved_at=None,
        )
        db.session.add(event)

    db.session.flush()
    return {
        "event": event.to_dict(),
        "budget": auction_budget_view(session),
        "lot": lot.to_dict(),
    }


def resolve_bid_outcome(
    *,
    session: GameSession,
    lot: Lot,
    outcome: str,
    event_id: int | None = None,
) -> Dict[str, Any]:
    if int(lot.session_id) != int(session.id):
        raise ValueError("Лот не принадлежит сессии")

    outcome_norm = _norm(outcome)
    if outcome_norm not in {"won", "lost"}:
        raise ValueError("outcome должен быть won или lost")

    event: Optional[AuctionEvent] = None
    if event_id is not None:
        event = db.session.get(AuctionEvent, int(event_id))
    if event is None:
        event = _latest_pending_bid_event(session_id=int(session.id), lot_id=int(lot.id))
    if (
        event is None
        or int(event.session_id) != int(session.id)
        or int(event.lot_id) != int(lot.id)
    ):
        raise ValueError("Не найдена pending-ставка для этого лота")
    if str(event.action or "") != "bid" or str(event.outcome or "") != "pending":
        raise ValueError("Событие ставки уже закрыто")

    amount = max(0.0, float(event.amount or 0.0))
    event.resolved_at = _utcnow()

    if outcome_norm == "lost":
        allpay_applied = _event_allpay_allowed(session=session, event=event, lot=lot)
        if allpay_applied and amount > _allpay_remaining(session) + 1e-9:
            raise ValueError("Недостаточно All-Pay бюджета для фиксации проигранной ставки")
        if allpay_applied:
            session.allpay_spent = max(0.0, float(session.allpay_spent or 0.0) + amount)
            event.budget_effect = float(-amount)
        else:
            event.budget_effect = 0.0
        event.outcome = "lost"
        event.details_json = {
            **dict(event.details_json or {}),
            "allpay_applied": bool(allpay_applied),
            "allpay_budget_remaining_after": float(_allpay_remaining(session) if allpay_applied else _allpay_remaining(session)),
        }
        if str(lot.status or "") == "available":
            lot.status = "rejected"
            db.session.add(lot)
        db.session.add(session)
        db.session.add(event)
        mark_stale_for_session(session.id, reason="auction_lost")
        db.session.flush()
        return {
            "event": event.to_dict(),
            "budget": auction_budget_view(session),
            "lot": lot.to_dict(),
            "purchase": None,
        }

    purchase = buy_lot(session, lot, amount)
    event.outcome = "won"
    event.budget_effect = float(-amount)
    event.details_json = {
        **dict(event.details_json or {}),
        "purchase_price": float(amount),
        "purchase_lot_status": str(lot.status or ""),
    }
    db.session.add(event)
    db.session.flush()
    return {
        "event": event.to_dict(),
        "budget": auction_budget_view(session),
        "lot": lot.to_dict(),
        "purchase": purchase,
    }


__all__ = [
    "apply_auction_action",
    "auction_budget_view",
    "list_auction_events",
    "resolve_bid_outcome",
]
