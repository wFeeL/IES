from __future__ import annotations

from typing import Any, Dict

from ..web.extensions import db
from ..web.models import GameSession, Ruleset
def load_session_or_none(session_id: int) -> GameSession | None:
    return db.session.get(GameSession, session_id)


def create_session_record(payload: Dict[str, Any]) -> GameSession:
    title = str(payload.get("title", "Новая сессия")).strip() or "Новая сессия"
    ruleset_id = payload.get("ruleset_id")
    ruleset: Ruleset | None = None
    if ruleset_id is None:
        ruleset = db.session.query(Ruleset).filter_by(is_active=True).first()
        if ruleset is None:
            raise ValueError("Нет активного набора правил")
        ruleset_id = ruleset.id
    else:
        ruleset = db.session.get(Ruleset, int(ruleset_id))
        if ruleset is None:
            raise ValueError(f"Ruleset {ruleset_id} not found")

    cfg = dict((ruleset.config_json or {}) if ruleset is not None else {})
    auction_cfg = dict(cfg.get("auction", {}) or {})
    default_budget = float(auction_cfg.get("starting_budget", 200.0) or 200.0)
    budget_raw = payload.get("budget_total", None)
    budget_value = float(default_budget if budget_raw is None else budget_raw)

    row = GameSession(
        title=title,
        ruleset_id=int(ruleset_id),
        selected_strategy=str(payload.get("selected_strategy", "balanced")),
        selected_forecast_id=payload.get("selected_forecast_id"),
        budget_total=budget_value,
        allpay_spent=float(payload.get("allpay_spent", 0.0) or 0.0),
    )
    db.session.add(row)
    db.session.commit()
    return row


def delete_session_record(session: GameSession) -> str:
    title = session.title
    db.session.delete(session)
    db.session.commit()
    return title
