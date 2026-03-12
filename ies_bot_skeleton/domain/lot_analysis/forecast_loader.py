from __future__ import annotations

from typing import Dict, Iterable

from ies_bot_skeleton.common.forecast_loader import load_forecast_bundle, lookup_pack_value

ForecastSeries = Dict[str, Dict[int, float]]
ForecastPack = Dict[str, ForecastSeries]


def load_forecasts(folder: str) -> ForecastPack:
    bundle = load_forecast_bundle(
        csv_dir=folder,
        psm=None,
        allow_psm_fallback=False,
        require_any=False,
    )
    return bundle.to_pack()


def lookup(
    forecasts: ForecastPack, kind: str, keys: Iterable[str], tick: int, default: float = 0.0
) -> float:
    out = lookup_pack_value(forecasts, kind, keys, tick, default=default)
    return float(default if out is None else out)
