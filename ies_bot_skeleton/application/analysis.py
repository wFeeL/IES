from __future__ import annotations

from typing import Dict, Iterable

from flask import current_app

from ..web.models import Forecast, GameSession, Lot
from ..web.services.evaluation import evaluate_lot, rank_lots


def evaluate_session_lot(
    *,
    session: GameSession,
    lot: Lot,
    strategy: str | None = None,
    forecast: Forecast | None = None,
    persist: bool = True,
) -> Dict[str, object]:
    current_app.logger.info(
        "evaluate_session_lot session_id=%s lot_id=%s persist=%s strategy=%s",
        session.id,
        lot.id,
        persist,
        strategy or "unified",
    )
    return evaluate_lot(
        session=session,
        lot=lot,
        strategy=strategy,
        forecast=forecast,
        persist=persist,
    )


def rank_session_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: str | None = None,
    forecast: Forecast | None = None,
    persist: bool = False,
) -> list[Dict[str, object]]:
    lot_list = list(lots)
    current_app.logger.info(
        "rank_session_lots session_id=%s count=%s persist=%s strategy=%s",
        session.id,
        len(lot_list),
        persist,
        strategy or "unified",
    )
    return rank_lots(
        session=session,
        lots=lot_list,
        strategy=strategy,
        forecast=forecast,
        persist=persist,
    )
