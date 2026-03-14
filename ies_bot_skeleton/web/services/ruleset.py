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
    merged.setdefault(
        "evaluation",
        {
            "weighted_expected": {
                "base": 0.50,
                "worst": 0.35,
                "best": 0.15,
                "custom": 0.0,
            },
            "risk_lambda": 0.25,
            "volatility_lambda": 0.15,
            "reserve_margin_abs": 5.0,
            "reserve_margin_share": 0.10,
            "storage_operating_cost_per_mwh_throughput": 0.0,
        },
    )
    merged.setdefault("auction", {}).setdefault("pwin_default", 0.35)
    merged.setdefault("auction", {}).setdefault("starting_budget", 200.0)
    return merged


def strategy_weights(config: Dict[str, Any], strategy: str) -> Dict[str, float]:
    base = dict(DEFAULT_WEIGHTS)
    base.update(config.get("lot_score_weights", {}) or {})

    profiles = dict(DEFAULT_STRATEGY_PROFILES)
    profiles.update(config.get("strategy_profiles", {}) or {})
    overlay = profiles.get(strategy, {}) or {}
    base.update(overlay)
    return {k: float(v) for k, v in base.items()}
