from __future__ import annotations

from typing import Dict, Iterable

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
    return rank_lots(
        session=session,
        lots=lots,
        strategy=strategy,
        forecast=forecast,
        persist=persist,
    )
