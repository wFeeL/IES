from __future__ import annotations

from typing import Any, Dict


DEFAULT_IES2026_CONFIG: Dict[str, Any] = {
    "time": {"horizon_ticks": 48},
    "auction": {
        "consumer_competitiveness_share": 0.45,
        "provider_margin_share": 0.18,
        "allpay_mode": "special_only",
    },
    "consumer": {
        "demand_elasticity": {
            "house_a": {"reference_tariff": 6.0, "elasticity": 0.18, "min_multiplier": 0.78},
            "house_b": {"reference_tariff": 6.5, "elasticity": 0.16, "min_multiplier": 0.8},
            "office": {"reference_tariff": 7.5, "elasticity": 0.12, "min_multiplier": 0.85},
            "factory": {"reference_tariff": 8.0, "elasticity": 0.08, "min_multiplier": 0.9},
            "hospital": {"reference_tariff": 9.5, "elasticity": 0.03, "min_multiplier": 0.96},
        },
        "unserved_penalty_per_mw_tick": {
            "house_a": 2.5,
            "house_b": 2.5,
            "office": 4.0,
            "factory": 9.0,
            "hospital": 14.0,
        },
    },
    "generation": {
        "solar": {"default_capacity_mw": 22.0, "default_efficiency": 0.94},
        "wind": {
            "rated_power_mw": 18.0,
            "cut_in_mps": 3.0,
            "rated_mps": 11.0,
            "cut_out_mps": 25.0,
        },
    },
    "storage": {
        "capacity_mw_tick": 120.0,
        "charge_rate_mw_tick": 15.0,
        "discharge_rate_mw_tick": 20.0,
        "roundtrip_efficiency": 0.93,
        "reserve_floor_share": 0.2,
        "reserve_credit_per_mw_tick": 0.35,
    },
    "market": {
        "default_buy_price": 9.0,
        "default_sell_price": 6.5,
        "default_balancing_penalty_price": 3.0,
        "low_price_sale_factor": 0.55,
        "sale_ramp_limit_mw": 8.0,
        "sale_risk_buffer_mw": 1.5,
        "bid_cap_mw": 120.0,
    },
    "network": {
        "base_edge_loss_pct": 1.5,
        "depth_loss_pct": 1.1,
        "district_cross_loss_pct": 0.5,
        "default_connection_point": "A",
        "connection_loss_pct_by_point": {"A": 0.0, "B": 1.5, "C": 3.0, "D": 4.5},
    },
    "scenarios": {
        "base": {
            "illumination": 1.0,
            "wind": 1.0,
            "load": 1.0,
            "sell_price": 1.0,
            "buy_price": 1.0,
        },
        "best": {
            "illumination": 1.12,
            "wind": 1.14,
            "load": 0.96,
            "sell_price": 1.06,
            "buy_price": 0.97,
        },
        "worst": {
            "illumination": 0.82,
            "wind": 0.88,
            "load": 1.08,
            "sell_price": 0.91,
            "buy_price": 1.08,
        },
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def ies2026_config(raw_ruleset: Dict[str, Any] | None) -> Dict[str, Any]:
    base = _deep_merge(DEFAULT_IES2026_CONFIG, {})
    if not isinstance(raw_ruleset, dict):
        return base
    out = _deep_merge(base, raw_ruleset)
    time_cfg = dict(out.get("time") or {})
    time_cfg["horizon_ticks"] = int(time_cfg.get("horizon_ticks", 48) or 48)
    out["time"] = time_cfg
    return out


def scenario_coefficients(config: Dict[str, Any], label: str) -> Dict[str, float]:
    scenarios = dict(config.get("scenarios") or {})
    selected = dict(scenarios.get(label) or {})
    return {
        "illumination": float(selected.get("illumination", 1.0) or 1.0),
        "wind": float(selected.get("wind", 1.0) or 1.0),
        "load": float(selected.get("load", 1.0) or 1.0),
        "sell_price": float(selected.get("sell_price", 1.0) or 1.0),
        "buy_price": float(selected.get("buy_price", 1.0) or 1.0),
    }
