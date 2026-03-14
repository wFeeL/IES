from __future__ import annotations

from typing import Dict, Iterable

from ..web.models import Forecast, GameSession, Lot
from ..web.services.evaluation import recommend_best_lot, strategy_fit


def recommend_for_session(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: str | None = None,
    forecast: Forecast | None = None,
) -> Dict[str, object]:
    return recommend_best_lot(
        session=session,
        lots=lots,
        strategy=strategy,
        forecast=forecast,
    )


def strategy_fit_for_lot(
    *,
    session: GameSession,
    lot: Lot,
    forecast: Forecast | None = None,
) -> Dict[str, object]:
    return strategy_fit(
        session=session,
        lot=lot,
        forecast=forecast,
    )
