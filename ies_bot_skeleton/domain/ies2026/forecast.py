from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .types import ForecastDataset, ForecastTick

LOAD_ALIASES = {
    "house_a": ("house_a", "housea", "house_load", "house"),
    "house_b": ("house_b", "houseb"),
    "office": ("office", "office_load"),
    "factory": ("factory", "factory_load"),
    "hospital": ("hospital", "hospital_load"),
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _collect_ticks(pack: Dict[str, Dict[str, Dict[int, float]]]) -> List[int]:
    ticks: set[int] = set()
    for bucket in pack.values():
        if not isinstance(bucket, dict):
            continue
        for series in bucket.values():
            if not isinstance(series, dict):
                continue
            ticks.update(int(tick) for tick in series.keys())
    return sorted(ticks)


def _series_value(series: Dict[int, float] | None, tick: int) -> float:
    if not isinstance(series, dict):
        return 0.0
    return _as_float(series.get(int(tick), 0.0), 0.0)


def _demand_series(
    load_bucket: Dict[str, Dict[int, float]],
    canonical_key: str,
) -> Dict[int, float]:
    for alias in LOAD_ALIASES.get(canonical_key, (canonical_key,)):
        if alias in load_bucket and isinstance(load_bucket[alias], dict):
            return {int(tick): _as_float(value, 0.0) for tick, value in load_bucket[alias].items()}
    return {}


def dataset_from_pack(
    pack: Dict[str, Dict[str, Dict[int, float]]] | None,
    *,
    config: Dict[str, Any],
) -> ForecastDataset:
    data = dict(pack or {})
    market_cfg = dict(config.get("market") or {})
    horizon = int((config.get("time") or {}).get("horizon_ticks", 48) or 48)
    discovered_ticks = _collect_ticks(data)
    ticks = list(range(horizon))

    wind_bucket = dict(data.get("wind") or {})
    load_bucket = dict(data.get("load") or {})
    market_bucket = dict(data.get("market") or {})
    illumination_series = dict(data.get("solar", {}) or {}).get("solar") or {}
    buy_series = market_bucket.get("price") or market_bucket.get("buy_price") or {}
    sell_series = market_bucket.get("sell_price") or {}
    balancing_series = market_bucket.get("balancing_penalty_price") or market_bucket.get(
        "balancing_penalty"
    ) or {}

    wind_channels = sorted(str(key) for key in wind_bucket.keys() if str(key).strip())
    if not wind_channels and wind_bucket.get("wind"):
        wind_channels = ["wind"]

    rows: List[ForecastTick] = []
    for tick in ticks:
        demand = {
            canonical: _series_value(_demand_series(load_bucket, canonical), tick)
            for canonical in LOAD_ALIASES
        }
        rows.append(
            ForecastTick(
                tick=int(tick),
                illumination=_series_value(illumination_series, tick),
                market_buy_price=_series_value(
                    buy_series,
                    tick,
                )
                or float(market_cfg.get("default_buy_price", 9.0) or 9.0),
                market_sell_price=_series_value(
                    sell_series,
                    tick,
                )
                or float(market_cfg.get("default_sell_price", 6.5) or 6.5),
                balancing_penalty_price=_series_value(
                    balancing_series,
                    tick,
                )
                or float(market_cfg.get("default_balancing_penalty_price", 3.0) or 3.0),
                demand_by_type=demand,
                wind_channels={
                    channel: _series_value(dict(wind_bucket.get(channel) or {}), tick)
                    for channel in wind_channels
                },
                extra_market={
                    "bid_cap_mw": _series_value(
                        market_bucket.get("bid_cap_mw") or market_bucket.get("bid_cap") or {},
                        tick,
                    )
                    or float(market_cfg.get("bid_cap_mw", 120.0) or 120.0),
                },
            )
        )
    return ForecastDataset(
        ticks=rows,
        wind_channels=wind_channels,
        assumptions={
            "horizon_ticks": horizon,
            "wind_channel_mode": "per_turbine_supported",
            "load_keys": list(LOAD_ALIASES.keys()),
            "canonical_series": {
                "solar": ["illumination"],
                "wind": wind_channels,
                "load": list(LOAD_ALIASES.keys()),
                "discovered_ticks": discovered_ticks,
            },
        },
    )


def average_series(rows: Iterable[ForecastTick], key: str) -> float:
    values: List[float] = []
    for row in rows:
        if key == "illumination":
            values.append(float(row.illumination))
        elif key == "market_buy_price":
            values.append(float(row.market_buy_price))
        elif key == "market_sell_price":
            values.append(float(row.market_sell_price))
    if not values:
        return 0.0
    return float(sum(values) / len(values))
