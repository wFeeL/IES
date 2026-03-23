from __future__ import annotations

from typing import Any, Dict


DEFAULT_IES2026_CONFIG: Dict[str, Any] = {
    "time": {"horizon_ticks": 48},
    "auction": {
        "parallel_lots_limit": 3,
        "consumer_competitiveness_share": 0.45,
        "provider_margin_share": 0.18,
        "allpay_mode": "special_only",
        "allpay_limit": 5000.0,
        "tie_break_threshold": 0.5,
        "repeat_increment": 0.1,
        "round_reset_drop_limit": 1,
        "modes_supported": [
            "ordinary_tariff_auction",
            "fixed_tariff_package_all_pay",
            "tie_break_all_pay",
        ],
    },
    "consumer": {
        "tariff_model": "fixed_connection_tariff_per_tick",
        "elasticity_profiles": ["low", "base", "high"],
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
        "max_power_mw": 20.0,
        "solar": {
            "default_capacity_mw": 20.0,
            "default_efficiency": 0.94,
            "orientation_hook_enabled": True,
        },
        "wind": {
            "max_power_mw": 20.0,
            "rated_power_mw": 18.0,
            "cut_in_mps": 3.0,
            "rated_mps": 11.0,
            "cut_out_mps": 25.0,
            "storm_shutdown_enabled": True,
            "hysteresis_hook_enabled": True,
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
        "max_exchange_bids": 100,
        "exchange_price_min": 2.0,
        "exchange_price_max": 20.0,
        "anti_dumping_scaler": 1.2,
        "anti_dumping_buffer_mw": 10.0,
        "sale_risk_buffer_mw": 1.5,
        "market_model": "aggregate_exchange_with_gp_fallback",
    },
    "network": {
        "base_edge_loss_pct": 1.5,
        "depth_loss_pct": 1.1,
        "district_cross_loss_pct": 0.5,
        "default_connection_point": "A",
        "connection_loss_pct_by_point": {"A": 0.0, "B": 1.5, "C": 3.0, "D": 4.5},
        "installation_priority": ["global_solar", "global_wind", "local_wind", "local_solar"],
    },
    "objects": {
        "scopes_supported": ["start", "local", "global"],
        "availability_by_scope": {
            "start": {"main_substation": 1, "mini_substation": 1, "house_a": 1},
            "local": {
                "mini_substation": 6,
                "solar": 6,
                "wind": 6,
                "storage": 6,
                "house_a": 8,
                "house_b": 8,
                "office": 6,
                "factory": 4,
                "hospital": 3,
            },
            "global": {
                "mini_substation": 6,
                "solar": 6,
                "wind": 6,
                "storage": 6,
                "house_a": 6,
                "house_b": 6,
                "office": 5,
                "factory": 4,
                "hospital": 2,
            },
        },
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
        out = base
    else:
        out = _deep_merge(base, raw_ruleset)
    time_cfg = dict(out.get("time") or {})
    time_cfg["horizon_ticks"] = int(time_cfg.get("horizon_ticks", 48) or 48)
    out["time"] = time_cfg
    generation_cfg = dict(out.get("generation") or {})
    solar_cfg = dict(generation_cfg.get("solar") or {})
    wind_cfg = dict(generation_cfg.get("wind") or {})
    max_power_mw = float(generation_cfg.get("max_power_mw", 20.0) or 20.0)
    solar_cfg["default_capacity_mw"] = min(
        max_power_mw,
        float(solar_cfg.get("default_capacity_mw", 20.0) or 20.0),
    )
    wind_cfg["rated_power_mw"] = min(
        float(wind_cfg.get("max_power_mw", max_power_mw) or max_power_mw),
        float(wind_cfg.get("rated_power_mw", 18.0) or 18.0),
    )
    generation_cfg["solar"] = solar_cfg
    generation_cfg["wind"] = wind_cfg
    out["generation"] = generation_cfg
    storage_cfg = dict(out.get("storage") or {})
    storage_cfg["capacity_mw_tick"] = 120.0
    storage_cfg["charge_rate_mw_tick"] = 15.0
    storage_cfg["discharge_rate_mw_tick"] = 20.0
    out["storage"] = storage_cfg
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


def anti_dumping_cap_mw(previous_useful_energy_mw: float, config: Dict[str, Any]) -> float:
    market_cfg = dict(config.get("market") or {})
    scaler = float(market_cfg.get("anti_dumping_scaler", 1.2) or 1.2)
    buffer = float(market_cfg.get("anti_dumping_buffer_mw", 10.0) or 10.0)
    return max(0.0, float(previous_useful_energy_mw) * scaler + buffer)
