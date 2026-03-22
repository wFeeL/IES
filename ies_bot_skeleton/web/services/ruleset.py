from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_WEIGHTS = {
    "w1_economy": 0.32,
    "w2_balance": 0.18,
    "w3_stability": 0.12,
    "w4_green": 0.08,
    "w5_flex": 0.10,
    "w6_risk": 0.10,
    "w7_wear": 0.05,
    "w8_violations": 0.05,
}

DEFAULT_STRATEGY_PROFILES = {
    "generation": {"w2_balance": 0.22, "w1_economy": 0.30},
    "consumer": {"w1_economy": 0.38, "w2_balance": 0.22},
    "balanced": {},
    "storage": {"w5_flex": 0.18, "w6_risk": 0.12},
    "eco": {"w4_green": 0.20, "w1_economy": 0.24},
    "risk_averse": {"w6_risk": 0.20, "w3_stability": 0.18},
    "aggressive": {"w1_economy": 0.42, "w6_risk": 0.06},
}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: Dict[str, Any], addon: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in addon.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def build_default_ruleset_config() -> Dict[str, Any]:
    game_cfg = _load_json(ROOT / "resources" / "lot_analysis" / "config_game.json")
    scoring_cfg = _load_yaml_or_json(ROOT / "config" / "scoring.yaml")
    merged = _deep_merge(game_cfg, scoring_cfg)
    merged.setdefault("lot_score_weights", dict(DEFAULT_WEIGHTS))
    merged.setdefault("strategy_profiles", dict(DEFAULT_STRATEGY_PROFILES))
    evaluation_cfg = dict(merged.get("evaluation", {}) or {})
    weighted_expected = dict(evaluation_cfg.get("weighted_expected", {}) or {})
    weighted_expected.setdefault("base", 0.50)
    weighted_expected.setdefault("worst", 0.35)
    weighted_expected.setdefault("best", 0.15)
    weighted_expected.setdefault("custom", 0.0)
    evaluation_cfg["weighted_expected"] = weighted_expected
    evaluation_cfg.setdefault("risk_lambda", 0.25)
    evaluation_cfg.setdefault("volatility_lambda", 0.15)
    evaluation_cfg.setdefault("reserve_margin_abs", 5.0)
    evaluation_cfg.setdefault("reserve_margin_share", 0.10)
    evaluation_cfg.setdefault("storage_operating_cost_per_mwh_throughput", 0.0)
    evaluation_cfg.setdefault("enable_strategy_profiles", False)
    evaluation_cfg.setdefault("ignore_connection_sectors", False)
    evaluation_cfg.setdefault("analysis_mode", "unified")
    merged["evaluation"] = evaluation_cfg
    auction_cfg = dict(merged.get("auction", {}) or {})
    auction_cfg.setdefault("pwin_default", 0.35)
    auction_cfg.setdefault("pwin_min", 0.08)
    auction_cfg.setdefault("pwin_max", 0.88)
    auction_cfg.setdefault("serious_competitors_default", 3)
    auction_cfg.setdefault("serious_competitors_min", 2)
    auction_cfg.setdefault("serious_competitors_max", 7)
    auction_cfg.setdefault("rank_weight", 0.22)
    auction_cfg.setdefault("synergy_weight", 0.12)
    auction_cfg.setdefault("scarcity_weight", 0.08)
    auction_cfg.setdefault("scope_weight", 0.06)
    auction_cfg.setdefault("safe_multiplier", 0.82)
    auction_cfg.setdefault("balanced_multiplier", 1.0)
    auction_cfg.setdefault("aggressive_multiplier", 1.22)
    auction_cfg.setdefault("volatility_lambda_bid", 0.15)
    auction_cfg.setdefault("auction_bid_model_version", "strategic_anchor_v4")
    auction_cfg.setdefault("conservative_utility_method", "weighted_expected_minus_volatility")
    auction_cfg.setdefault("scope_signal_global", 0.65)
    auction_cfg.setdefault("scope_signal_local", -0.45)
    auction_cfg.setdefault("block_on_topology_invalid", False)
    auction_cfg.setdefault("starting_budget", 200.0)
    merged["auction"] = auction_cfg
    return merged


def strategy_weights(config: Dict[str, Any], strategy: str | None = None) -> Dict[str, float]:
    selected_strategy = str(strategy or "")
    base = dict(DEFAULT_WEIGHTS)
    base.update(config.get("lot_score_weights", {}) or {})

    evaluation_cfg = dict(config.get("evaluation", {}) or {})
    enable_strategy_profiles = bool(evaluation_cfg.get("enable_strategy_profiles", False))
    if enable_strategy_profiles:
        profiles = dict(DEFAULT_STRATEGY_PROFILES)
        profiles.update(config.get("strategy_profiles", {}) or {})
        overlay = profiles.get(selected_strategy, {}) or {}
        base.update(overlay)
    return {k: float(v) for k, v in base.items()}
