from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class EVResult:
    v: float
    pwin: float
    bid: float
    ev: float
    bid_max_ev_nonneg: float


def ev_allpay(v: float, pwin: float, bid: float) -> EVResult:
    bid_max = max(0.0, pwin * v)
    return EVResult(
        v=float(v),
        pwin=float(pwin),
        bid=float(bid),
        ev=float(pwin * v - bid),
        bid_max_ev_nonneg=bid_max,
    )


def recommended_bid_range(v: float, pwin: float, safety: float = 0.80) -> Tuple[float, float]:
    vmax = max(0.0, safety * pwin * v)
    vmin = 0.4 * vmax
    return vmin, vmax


__all__ = ["EVResult", "ev_allpay", "recommended_bid_range"]
