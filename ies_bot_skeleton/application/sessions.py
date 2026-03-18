from __future__ import annotations

from typing import Any, Dict

from ..web.extensions import db
from ..web.models import GameSession, Ruleset
from ..web.services.test_game_preset import (
    TEST_GAME_DEFAULT_SESSION_TITLE,
    bootstrap_test_game_session,
    preferred_default_ruleset,
)


def load_session_or_none(session_id: int) -> GameSession | None:
    return db.session.get(GameSession, session_id)


def _validate_selected_forecast_for_new_session(raw_value: Any) -> None:
    if raw_value in (None, "", 0, "0"):
        return
    raise ValueError("Нельзя задавать selected_forecast_id при создании новой сессии")


def create_session_record(payload: Dict[str, Any]) -> GameSession:
    title = str(payload.get("title", TEST_GAME_DEFAULT_SESSION_TITLE)).strip()
    title = title or TEST_GAME_DEFAULT_SESSION_TITLE
    _validate_selected_forecast_for_new_session(payload.get("selected_forecast_id"))
    ruleset_id = payload.get("ruleset_id")
    ruleset: Ruleset | None = None
    if ruleset_id is None:
        ruleset = preferred_default_ruleset()
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
        selected_strategy="unified",
        selected_forecast_id=None,
        budget_total=budget_value,
        allpay_spent=float(payload.get("allpay_spent", 0.0) or 0.0),
    )
    db.session.add(row)
    try:
        db.session.flush()
        bootstrap_test_game_session(row, commit=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return row


def delete_session_record(session: GameSession) -> str:
    title = session.title
    db.session.delete(session)
    db.session.commit()
    return title
