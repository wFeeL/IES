from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Iterable

try:
    from ies_bot_skeleton.common.forecast_loader import load_forecast_bundle, lookup_pack_value
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[5]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
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


def lookup(forecasts: ForecastPack, kind: str, keys: Iterable[str], tick: int, default: float = 0.0) -> float:
    out = lookup_pack_value(forecasts, kind, keys, tick, default=default)
    return float(default if out is None else out)
