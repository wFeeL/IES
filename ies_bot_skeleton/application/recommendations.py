from __future__ import annotations

from typing import Any, Dict, Iterable

from ..web.models import Forecast, GameSession, Lot
from ..web.services.evaluation import recommend_best_lot, strategy_fit


def recommend_for_session(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    mode: str,
    strategy: str | None = None,
    forecast: Forecast | None = None,
    corridor_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return recommend_best_lot(
        session=session,
        lots=lots,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
        corridor_override=corridor_override,
    )


def strategy_fit_for_lot(
    *,
    session: GameSession,
    lot: Lot,
    mode: str,
    forecast: Forecast | None = None,
    corridor_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return strategy_fit(
        session=session,
        lot=lot,
        mode=mode,
        forecast=forecast,
        corridor_override=corridor_override,
    )
