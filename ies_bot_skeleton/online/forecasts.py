from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from ..common.forecast_loader import load_forecast_bundle, lookup_pack_value

ForecastSeries = Dict[str, Dict[int, float]]
ForecastPack = Dict[str, ForecastSeries]


def load_forecasts(psm: Any = None) -> ForecastPack:
    bundle = load_forecast_bundle(
        csv_dir=".",
        psm=psm,
        allow_psm_fallback=True,
        require_any=False,
    )
    return bundle.to_pack()


def lookup_forecast(
    forecasts: ForecastPack,
    kind: str,
    key_candidates: Iterable[str],
    tick: int,
    default: Optional[float] = None,
) -> Optional[float]:
    return lookup_pack_value(forecasts, kind, key_candidates, tick, default=default)
