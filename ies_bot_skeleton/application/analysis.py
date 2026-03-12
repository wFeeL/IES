from __future__ import annotations

from typing import Any, Dict, Iterable

from ..web.models import Forecast, GameSession, Lot
from ..web.services.evaluation import compare_lots, evaluate_lot


def evaluate_session_lot(
    *,
    session: GameSession,
    lot: Lot,
    mode: str,
    strategy: str | None = None,
    forecast: Forecast | None = None,
    corridor_override: Dict[str, Any] | None = None,
    persist: bool = True,
) -> Dict[str, Any]:
    return evaluate_lot(
        session=session,
        lot=lot,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
        corridor_override=corridor_override,
        persist=persist,
    )


def compare_session_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    mode: str,
    strategy: str | None = None,
    forecast: Forecast | None = None,
    corridor_override: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    return compare_lots(
        session=session,
        lots=lots,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
        corridor_override=corridor_override,
    )
