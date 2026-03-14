from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from ..extensions import db
from ..models import EvaluationResult, GameSession, LotItem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _append_reason(current: str, reason: str) -> str:
    cleaned_current = (current or "").strip()
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        return cleaned_current
    if not cleaned_current:
        return cleaned_reason
    if cleaned_reason in cleaned_current:
        return cleaned_current
    return f"{cleaned_current}; {cleaned_reason}"


def mark_results_stale(results: Iterable[EvaluationResult], reason: str) -> int:
    now = _utcnow()
    updated = 0
    for row in results:
        new_reason = _append_reason(row.stale_reason or "", reason)
        needs_update = (not bool(row.is_stale)) or (new_reason != (row.stale_reason or ""))
        if not needs_update:
            continue
        row.is_stale = True
        row.stale_reason = new_reason
        row.stale_marked_at = now
        db.session.add(row)
        updated += 1
    if updated:
        db.session.commit()
    return updated


def mark_stale_for_ruleset(ruleset_id: int, *, reason: str = "ruleset_changed") -> int:
    rows = (
        db.session.query(EvaluationResult)
        .join(GameSession, GameSession.id == EvaluationResult.session_id)
        .filter(GameSession.ruleset_id == int(ruleset_id))
        .all()
    )
    return mark_results_stale(rows, reason)


def mark_stale_for_object_type(
    object_type_id: int,
    *,
    reason: str = "object_type_changed",
) -> int:
    lot_ids = [
        lot_id
        for (lot_id,) in db.session.query(LotItem.lot_id)
        .filter(LotItem.object_type_id == int(object_type_id))
        .distinct()
        .all()
    ]
    if not lot_ids:
        return 0
    rows = db.session.query(EvaluationResult).filter(EvaluationResult.lot_id.in_(lot_ids)).all()
    return mark_results_stale(rows, reason)


def mark_stale_for_session(
    session_id: int,
    *,
    reason: str,
) -> int:
    rows = db.session.query(EvaluationResult).filter_by(session_id=int(session_id)).all()
    return mark_results_stale(rows, reason)


def stale_summary_for_session(session_id: int) -> Dict[str, Any]:
    rows = (
        db.session.query(EvaluationResult)
        .filter_by(session_id=int(session_id))
        .order_by(
            EvaluationResult.lot_id.asc(),
            EvaluationResult.created_at.desc(),
            EvaluationResult.id.desc(),
        )
        .all()
    )
    latest_by_lot: dict[int, EvaluationResult] = {}
    for row in rows:
        latest_by_lot.setdefault(int(row.lot_id), row)
    stale_rows = [row for row in latest_by_lot.values() if row.is_stale]
    total = len(stale_rows)
    last = max(
        stale_rows,
        key=lambda row: (row.stale_marked_at or _utcnow(), row.id),
        default=None,
    )
    return {
        "session_id": int(session_id),
        "stale_count": int(total),
        "has_stale": bool(total > 0),
        "last_reason": last.stale_reason if last is not None else "",
        "last_marked_at": (
            last.stale_marked_at.isoformat() if last and last.stale_marked_at else None
        ),
    }
