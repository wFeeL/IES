from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast

from ..extensions import db
from ..models import (
    EvaluationResult,
    Forecast,
    ForecastPeriod,
    GameSession,
    Lot,
    LotItem,
    ObjectInstance,
)
from ...common.budgeting import (
    allpay_spent_total,
    budget_snapshot,
    purchase_spent_total,
    remaining_budget as session_remaining_budget,
    spent_total as session_spent_total,
)
from .analysis_context import resolve_analysis_context
from .connection_advisor import recommend_connection_for_profile
from .forecast_service import load_bundled_forecast_pack
from .network import validate_session_network
from .network_readiness import network_readiness_summary
from .ruleset import strategy_weights

DEFAULT_WEIGHTED = {"base": 0.50, "worst": 0.35, "best": 0.15}
MIN_POSITIVE_PROFIT_FLOOR = 1.0
BUDGET_PRESERVATION_NOTE = (
    "Неиспользованный остаток бюджета сохраняется для следующих аукционов."
)
DEFAULT_AUCTION_BID_CFG = {
    "auction_bid_model_version": "pwin_v1",
    "conservative_utility_method": "weighted_expected_minus_volatility",
    "volatility_lambda_bid": 0.15,
    "pwin_default": 0.35,
    "pwin_min": 0.08,
    "pwin_max": 0.88,
    "serious_competitors_default": 3.0,
    "serious_competitors_min": 2.0,
    "serious_competitors_max": 7.0,
    "rank_weight": 0.22,
    "synergy_weight": 0.12,
    "scarcity_weight": 0.08,
    "scope_weight": 0.06,
    "safe_multiplier": 0.82,
    "balanced_multiplier": 1.0,
    "aggressive_multiplier": 1.22,
    "scope_signal_global": 0.65,
    "scope_signal_local": -0.45,
    "block_on_topology_invalid": False,
    "min_positive_profit_floor": MIN_POSITIVE_PROFIT_FLOOR,
}


class ForecastCompatibilityError(ValueError):
    def __init__(self, report: Dict[str, Any]) -> None:
        reasons = report.get("blocking_reasons") or []
        message = "; ".join(reasons) if reasons else "Прогноз несовместим с объектами сессии."
        super().__init__(message)
        self.report = dict(report)


@dataclass
class Asset:
    role: str
    code: str
    quantity: int
    parameters: Dict[str, Any]


@dataclass
class ScenarioSnapshot:
    income_total: float
    generation_income: float
    cost_total: float
    contracts: float
    fuel_and_taxes: float
    market_net: float
    penalties_total: float
    losses_total: float
    risk_penalty: float
    flexibility_value: float
    reserve_value: float
    eco_value: float
    net_profit: float
    utility_score: float
    served_load_revenue: float = 0.0
    avoided_market_purchase_value: float = 0.0
    export_revenue: float = 0.0
    market_purchase_cost: float = 0.0
    storage_operating_cost: float = 0.0
    overload_penalties: float = 0.0
    deficit_penalties: float = 0.0
    role_breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass
class DeltaSnapshot:
    delta_total: float
    delta_income: float
    delta_penalties: float
    delta_contracts: float
    delta_fuel_and_taxes: float
    delta_market_net: float
    delta_network_losses_cost: float
    delta_eco_value: float
    delta_risk_penalty: float
    delta_eco_points: float
    delta_served_load_revenue: float = 0.0
    delta_avoided_market_purchase_value: float = 0.0
    delta_export_revenue: float = 0.0
    delta_market_purchase_cost: float = 0.0
    delta_storage_operating_cost: float = 0.0
    delta_overload_penalties: float = 0.0
    delta_deficit_penalties: float = 0.0
    flags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    role_breakdown: Dict[str, float] = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip())


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _auction_bid_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict((cfg.get("auction", {}) or {}))
    out = dict(DEFAULT_AUCTION_BID_CFG)
    out.update(raw)
    return out


def _gross_profit_before_bid(net_profit_at_current_price: float, current_price: float) -> float:
    return max(0.0, float(net_profit_at_current_price) + max(0.0, float(current_price)))


def _retained_budget(remaining_budget: float, bid: float) -> float:
    return max(0.0, float(remaining_budget) - max(0.0, float(bid)))


def _profit_after_bid(gross_profit_before_bid: float, bid: float) -> float:
    return float(gross_profit_before_bid) - max(0.0, float(bid))


def conservative_utility(
    *,
    weighted_expected: float,
    net_profit_worst: float,
    net_profit_base: float,
    net_profit_best: float,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    auction_cfg = _auction_bid_cfg(cfg)
    scenario_values = [float(net_profit_worst), float(net_profit_base), float(net_profit_best)]
    sigma_profit = float(pstdev(scenario_values)) if len(set(scenario_values)) > 1 else 0.0
    method = str(
        auction_cfg.get("conservative_utility_method")
        or DEFAULT_AUCTION_BID_CFG["conservative_utility_method"]
    )
    volatility_lambda_bid = float(
        auction_cfg.get("volatility_lambda_bid", DEFAULT_AUCTION_BID_CFG["volatility_lambda_bid"])
    )
    expected_value = float(weighted_expected)
    if method == "weighted_expected":
        conservative_raw = expected_value
    else:
        conservative_raw = expected_value - volatility_lambda_bid * sigma_profit
    conservative_value = float(max(0.0, conservative_raw))
    return {
        "method": method,
        "weighted_expected": expected_value,
        "sigma_profit": float(sigma_profit),
        "volatility_lambda_bid": float(volatility_lambda_bid),
        "raw_value": float(conservative_raw),
        "value": conservative_value,
    }


def _lot_scope_signal(lots: Sequence[Lot], cfg: Dict[str, Any]) -> float:
    auction_cfg = _auction_bid_cfg(cfg)
    global_signal = float(
        auction_cfg.get("scope_signal_global", DEFAULT_AUCTION_BID_CFG["scope_signal_global"])
    )
    local_signal = float(
        auction_cfg.get("scope_signal_local", DEFAULT_AUCTION_BID_CFG["scope_signal_local"])
    )
    scopes = {_norm(lot.scope) for lot in lots}
    if "global" in scopes:
        return float(global_signal)
    if scopes and scopes <= {"local"}:
        return float(local_signal)
    if "local" in scopes:
        return float(local_signal * 0.5)
    return 0.0


def _lot_utility_proxy(lot: Lot) -> float:
    category_base = {
        "generator": 26.0,
        "storage": 20.0,
        "infrastructure": 14.0,
        "consumer": 16.0,
        "mixed": 18.0,
    }
    total = 0.0
    for item in _lot_items(lot):
        if item.object_type is None:
            continue
        qty = max(1, int(item.quantity or 1))
        params = dict(item.object_type.default_parameters_json or {})
        params.update(dict(item.overrides_json or {}))
        role = _asset_role(item.object_type.category, item.object_type.economic_role)
        category_weight = float(category_base.get(role, category_base["mixed"]))
        technical_value = 0.0
        technical_value += max(0.0, _as_float(params.get("generation_mw"), 0.0)) * 2.0
        technical_value += max(0.0, _as_float(params.get("expected_consumption_mw"), 0.0)) * 1.6
        technical_value += max(0.0, _as_float(params.get("capacity_mw_tick"), 0.0)) * 0.8
        technical_value += max(0.0, _as_float(params.get("ports"), 0.0)) * 0.7
        total += qty * (category_weight + technical_value)
    return float(total - max(0.0, _lot_reference_price(lot)) * 0.30)


def _bundle_utility_proxy(lots: Sequence[Lot]) -> float:
    return float(sum(_lot_utility_proxy(lot) for lot in lots))


def _rank_percentile_by_utility(
    *,
    target_utility: float,
    available_lots: Sequence[Lot],
) -> float:
    if not available_lots:
        return 0.5
    reference = [_lot_utility_proxy(lot) for lot in available_lots]
    if not reference:
        return 0.5
    less_equal = sum(1 for value in reference if float(value) <= float(target_utility))
    return _clamp(float(less_equal) / float(len(reference)), 0.0, 1.0)


def _scarcity_signal(
    *,
    lots: Sequence[Lot],
    available_lots: Sequence[Lot],
) -> float:
    demand: Dict[int, int] = {}
    for lot in available_lots:
        seen: set[int] = set()
        for item in _lot_items(lot):
            type_id = int(item.object_type_id or 0)
            if type_id <= 0 or type_id in seen:
                continue
            demand[type_id] = demand.get(type_id, 0) + 1
            seen.add(type_id)
    if not demand:
        return 0.0
    max_count = max(demand.values()) or 1
    scores: List[float] = []
    for lot in lots:
        for item in _lot_items(lot):
            type_id = int(item.object_type_id or 0)
            if type_id <= 0:
                continue
            used = demand.get(type_id, max_count)
            scarcity = 1.0 - float(used - 1) / float(max(max_count - 1, 1))
            scores.append(_clamp(scarcity, 0.0, 1.0))
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def estimate_lot_win_probability(
    *,
    lots: Sequence[Lot],
    session: GameSession,
    available_lots: Sequence[Lot],
    conservative_utility_value: float,
    portfolio_synergy: float,
) -> Dict[str, Any]:
    rules_cfg = dict(session.ruleset.config_json or {})
    auction_cfg = _auction_bid_cfg(rules_cfg)
    rank_pct = _rank_percentile_by_utility(
        target_utility=_bundle_utility_proxy(lots) + max(0.0, float(conservative_utility_value)),
        available_lots=available_lots,
    )
    normalized_rank_signal = _clamp((rank_pct - 0.5) * 2.0, -1.0, 1.0)
    denominator = max(1.0, abs(float(conservative_utility_value)))
    synergy_signal = _clamp(float(portfolio_synergy) / denominator, -1.0, 1.0)
    scarcity_signal = _scarcity_signal(lots=lots, available_lots=available_lots)
    scope_signal = _lot_scope_signal(lots, rules_cfg)
    pwin_base = float(auction_cfg.get("pwin_default", DEFAULT_AUCTION_BID_CFG["pwin_default"]))
    rank_weight = float(auction_cfg.get("rank_weight", DEFAULT_AUCTION_BID_CFG["rank_weight"]))
    synergy_weight = float(
        auction_cfg.get("synergy_weight", DEFAULT_AUCTION_BID_CFG["synergy_weight"])
    )
    scarcity_weight = float(
        auction_cfg.get("scarcity_weight", DEFAULT_AUCTION_BID_CFG["scarcity_weight"])
    )
    scope_weight = float(auction_cfg.get("scope_weight", DEFAULT_AUCTION_BID_CFG["scope_weight"]))
    pwin_min = float(auction_cfg.get("pwin_min", DEFAULT_AUCTION_BID_CFG["pwin_min"]))
    pwin_max = float(auction_cfg.get("pwin_max", DEFAULT_AUCTION_BID_CFG["pwin_max"]))
    p_win = _clamp(
        pwin_base
        + rank_weight * normalized_rank_signal
        + synergy_weight * synergy_signal
        + scarcity_weight * scarcity_signal
        + scope_weight * scope_signal,
        pwin_min,
        pwin_max,
    )
    return {
        "p_win": float(p_win),
        "pwin_default": float(pwin_base),
        "pwin_min": float(pwin_min),
        "pwin_max": float(pwin_max),
        "rank_percentile": float(rank_pct),
        "normalized_rank_signal": float(normalized_rank_signal),
        "synergy_signal": float(synergy_signal),
        "scarcity_signal": float(scarcity_signal),
        "scope_signal": float(scope_signal),
        "rank_weight": float(rank_weight),
        "synergy_weight": float(synergy_weight),
        "scarcity_weight": float(scarcity_weight),
        "scope_weight": float(scope_weight),
    }


def estimate_serious_competitors(
    *,
    lots: Sequence[Lot],
    session: GameSession,
    pwin_payload: Dict[str, Any],
) -> Dict[str, Any]:
    rules_cfg = dict(session.ruleset.config_json or {})
    auction_cfg = _auction_bid_cfg(rules_cfg)
    default = float(
        auction_cfg.get(
            "serious_competitors_default",
            DEFAULT_AUCTION_BID_CFG["serious_competitors_default"],
        )
    )
    lower = float(
        auction_cfg.get("serious_competitors_min", DEFAULT_AUCTION_BID_CFG["serious_competitors_min"])
    )
    upper = float(
        auction_cfg.get("serious_competitors_max", DEFAULT_AUCTION_BID_CFG["serious_competitors_max"])
    )
    rank_signal = float(pwin_payload.get("normalized_rank_signal", 0.0) or 0.0)
    synergy_signal = float(pwin_payload.get("synergy_signal", 0.0) or 0.0)
    scarcity_signal = float(pwin_payload.get("scarcity_signal", 0.0) or 0.0)
    scope_signal = float(pwin_payload.get("scope_signal", 0.0) or 0.0)
    rank_weight = float(pwin_payload.get("rank_weight", 0.0) or 0.0)
    synergy_weight = float(pwin_payload.get("synergy_weight", 0.0) or 0.0)
    scarcity_weight = float(pwin_payload.get("scarcity_weight", 0.0) or 0.0)
    scope_weight = float(pwin_payload.get("scope_weight", 0.0) or 0.0)
    scope_bonus = 0.4 if any(_norm(lot.scope) == "global" for lot in lots) else 0.0
    estimated = (
        default
        + max(0.0, rank_signal) * rank_weight * 2.8
        + max(0.0, synergy_signal) * synergy_weight * 2.2
        + max(0.0, scarcity_signal) * scarcity_weight * 2.0
        + scope_signal * scope_weight * 1.8
        + scope_bonus
    )
    serious = int(round(_clamp(estimated, lower, upper)))
    serious = max(int(round(lower)), serious)
    return {
        "estimated_raw": float(estimated),
        "serious_competitors": int(serious),
        "min": float(lower),
        "max": float(upper),
        "default": float(default),
    }


def build_bid_recommendations(
    *,
    conservative_utility_value: float,
    gross_profit_before_bid: float,
    remaining_budget: float,
    p_win: float,
    serious_competitors: int,
    cfg: Dict[str, Any],
) -> Dict[str, float]:
    auction_cfg = _auction_bid_cfg(cfg)
    utility = max(0.0, float(conservative_utility_value))
    gross_profit = max(0.0, float(gross_profit_before_bid))
    budget_cap = max(0.0, float(remaining_budget))
    safe_multiplier = float(
        auction_cfg.get("safe_multiplier", DEFAULT_AUCTION_BID_CFG["safe_multiplier"])
    )
    balanced_multiplier = float(
        auction_cfg.get("balanced_multiplier", DEFAULT_AUCTION_BID_CFG["balanced_multiplier"])
    )
    aggressive_multiplier = float(
        auction_cfg.get("aggressive_multiplier", DEFAULT_AUCTION_BID_CFG["aggressive_multiplier"])
    )
    positive_profit_floor = max(
        0.0,
        float(
            auction_cfg.get(
                "min_positive_profit_floor",
                DEFAULT_AUCTION_BID_CFG["min_positive_profit_floor"],
            )
        ),
    )
    k = max(1.0, float(int(serious_competitors)))
    base_share = float(((k - 1.0) / k) * _clamp(float(p_win), 0.0, 1.0))
    safe_raw = utility * base_share * safe_multiplier
    balanced_raw = utility * base_share * balanced_multiplier
    aggressive_raw = utility * base_share * aggressive_multiplier
    hard_ceiling_model = max(0.0, utility - positive_profit_floor)
    hard_ceiling_profit = max(0.0, gross_profit - positive_profit_floor)
    hard_ceiling_bid = min(hard_ceiling_model, hard_ceiling_profit)
    max_bid = min(hard_ceiling_bid, budget_cap)
    recommended_bid_safe = min(max(0.0, safe_raw), max_bid)
    recommended_bid_balanced = min(max(0.0, balanced_raw), max_bid)
    recommended_bid_aggressive = min(max(0.0, aggressive_raw), max_bid)
    recommended_bid_safe = min(recommended_bid_safe, recommended_bid_balanced)
    recommended_bid_aggressive = max(recommended_bid_balanced, recommended_bid_aggressive)
    recommended_bid_aggressive = min(recommended_bid_aggressive, max_bid)
    recommended_bid = recommended_bid_balanced
    return {
        "base_share": float(base_share),
        "safe_multiplier": float(safe_multiplier),
        "balanced_multiplier": float(balanced_multiplier),
        "aggressive_multiplier": float(aggressive_multiplier),
        "recommended_bid_safe": float(recommended_bid_safe),
        "recommended_bid_balanced": float(recommended_bid_balanced),
        "recommended_bid_aggressive": float(recommended_bid_aggressive),
        "recommended_bid": float(recommended_bid),
        "working_bid": float(recommended_bid_balanced),
        "hard_ceiling_bid": float(max(0.0, hard_ceiling_bid)),
        "max_bid": float(max(0.0, max_bid)),
        "net_profit_at_recommended_bid": float(_profit_after_bid(gross_profit, recommended_bid)),
        "net_profit_at_balanced_bid": float(
            _profit_after_bid(gross_profit, recommended_bid_balanced)
        ),
        "net_profit_at_safe_bid": float(_profit_after_bid(gross_profit, recommended_bid_safe)),
        "net_profit_at_aggressive_bid": float(
            _profit_after_bid(gross_profit, recommended_bid_aggressive)
        ),
        "net_profit_at_max_bid": float(_profit_after_bid(gross_profit, max_bid)),
        "remaining_budget_after_recommended_bid": float(
            _retained_budget(budget_cap, recommended_bid_balanced)
        ),
        "remaining_budget_after_balanced_bid": float(
            _retained_budget(budget_cap, recommended_bid_balanced)
        ),
        "remaining_budget_after_safe_bid": float(
            _retained_budget(budget_cap, recommended_bid_safe)
        ),
        "remaining_budget_after_aggressive_bid": float(
            _retained_budget(budget_cap, recommended_bid_aggressive)
        ),
        "remaining_budget_after_max_bid": float(_retained_budget(budget_cap, max_bid)),
    }


def _risk_band(*, p_worst: float, risk_ratio: float) -> str:
    if float(p_worst) <= 0.0 or float(risk_ratio) >= 0.60:
        return "high"
    if float(risk_ratio) >= 0.30:
        return "medium"
    return "low"


def _fit_multiplier(*, system_fit_score: float, evaluation_cfg: Dict[str, Any]) -> float:
    slope = float(evaluation_cfg.get("fit_multiplier_slope", 0.005))
    low = float(evaluation_cfg.get("fit_multiplier_min", 0.55))
    high = float(evaluation_cfg.get("fit_multiplier_max", 1.35))
    return _clamp(1.0 + float(system_fit_score) * slope, low, high)


def _reserve_requirements(
    *,
    remaining_budget: float,
    p_exp: float,
    evaluation_cfg: Dict[str, Any],
) -> float:
    reserve_abs = float(evaluation_cfg.get("reserve_budget_abs", 10.0))
    reserve_share = float(evaluation_cfg.get("reserve_budget_share", 0.12))
    baseline = max(0.0, float(remaining_budget), float(p_exp))
    return min(max(0.0, float(remaining_budget)), max(reserve_abs, reserve_share * baseline))


def _reserve_impact(
    *,
    reserve_required: float,
    remaining_budget: float,
    entry_price_total: float,
    evaluation_cfg: Dict[str, Any],
) -> float:
    reserve_penalty = float(evaluation_cfg.get("reserve_impact_lambda", 0.45))
    liquidity_after_entry = max(0.0, float(remaining_budget) - max(0.0, float(entry_price_total)))
    reserve_gap = max(0.0, float(reserve_required) - liquidity_after_entry)
    return float(reserve_gap * reserve_penalty)


def _opportunity_cost(
    *,
    entry_price_total: float,
    remaining_budget: float,
    marginal_value: float,
    evaluation_cfg: Dict[str, Any],
) -> float:
    if remaining_budget <= 0.0:
        return float(max(0.0, marginal_value))
    free_ratio = float(evaluation_cfg.get("opportunity_free_ratio", 0.35))
    slope = float(evaluation_cfg.get("opportunity_cost_slope", 0.40))
    burn_ratio = max(0.0, float(entry_price_total)) / max(float(remaining_budget), 1e-9)
    pressure = max(0.0, burn_ratio - free_ratio)
    return float(max(0.0, float(marginal_value)) * pressure * slope)


def _scarcity_phase_multiplier(
    *,
    p_win: float,
    scarcity_signal: float,
    available_lots_count: int,
    rules_cfg: Dict[str, Any],
    evaluation_cfg: Dict[str, Any],
) -> float:
    auction_cfg = _auction_bid_cfg(rules_cfg)
    scarcity_weight = float(evaluation_cfg.get("scarcity_multiplier_weight", 0.10))
    scarcity_component = 1.0 + scarcity_weight * _clamp(float(scarcity_signal), 0.0, 1.0)

    late_threshold = int(evaluation_cfg.get("phase_late_lots_threshold", 8))
    late_bonus = (
        float(evaluation_cfg.get("phase_late_bonus", 0.08))
        if int(max(0, available_lots_count)) <= max(1, late_threshold)
        else 0.0
    )
    pwin_discount = float(evaluation_cfg.get("pwin_value_discount", 0.18))
    pwin_component = 1.0 - pwin_discount * max(0.0, 0.50 - _clamp(float(p_win), 0.0, 1.0))
    scope_component = 1.0 + max(0.0, float(auction_cfg.get("scope_weight", 0.0))) * 0.02
    return _clamp(scarcity_component * (1.0 + late_bonus) * pwin_component * scope_component, 0.70, 1.40)


def _ignore_connection_sectors_cfg(cfg: Dict[str, Any]) -> bool:
    evaluation_cfg = dict((cfg.get("evaluation", {}) or {}))
    return bool(evaluation_cfg.get("ignore_connection_sectors", False))


def _ignore_connection_sectors(session: GameSession) -> bool:
    ruleset = getattr(session, "ruleset", None)
    rules_cfg = dict(getattr(ruleset, "config_json", {}) or {})
    return _ignore_connection_sectors_cfg(rules_cfg)


def _weighted_expected(config: Dict[str, Any], base: float, worst: float, best: float) -> float:
    weighted = ((config.get("evaluation", {}) or {}).get("weighted_expected", {}) or {}).copy()
    if not weighted:
        weighted = dict(DEFAULT_WEIGHTED)
    wb = float(weighted.get("base", DEFAULT_WEIGHTED["base"]))
    ww = float(weighted.get("worst", DEFAULT_WEIGHTED["worst"]))
    wbest = float(weighted.get("best", DEFAULT_WEIGHTED["best"]))
    return wb * base + ww * worst + wbest * best


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _session_objects(session: GameSession) -> Sequence[ObjectInstance]:
    return cast(Sequence[ObjectInstance], list(session.objects))


def _lot_items(lot: Lot) -> Sequence[LotItem]:
    return cast(Sequence[LotItem], list(lot.items))


def _lot_purchase_spent(session: GameSession) -> float:
    return purchase_spent_total(session)


def _allpay_spent(session: GameSession) -> float:
    return allpay_spent_total(session)


def _spent_total(session: GameSession) -> float:
    return session_spent_total(session)


def _lot_reference_price(lot: Lot) -> float:
    if str(lot.status or "") == "bought" and lot.purchase_price is not None:
        return float(lot.purchase_price or 0.0)
    return float(lot.current_bid or 0.0)


def _reserved_lot_spend(lots: Sequence[Lot] | None) -> float:
    total = 0.0
    for lot in lots or []:
        if str(lot.status or "") == "bought":
            continue
        total += _lot_reference_price(lot)
    return float(total)


def _count_lot_objects(lots: Sequence[Lot] | None) -> int:
    total = 0
    for lot in lots or []:
        for item in _lot_items(lot):
            total += max(1, int(item.quantity or 1))
    return total


def _remaining_budget(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    return session_remaining_budget(session, reserved_spend=reserved_spend)


def _portfolio_context(
    session: GameSession,
    *,
    reserved_spend: float = 0.0,
    extra_portfolio_lots: Sequence[Lot] | None = None,
) -> Dict[str, Any]:
    budget = budget_snapshot(session, reserved_spend=reserved_spend)
    return {
        "analysis_mode": "unified",
        "bought_lots_count": sum(
            1 for lot in _session_lots(session) if str(lot.status or "") == "bought"
        )
        + len(list(extra_portfolio_lots or [])),
        **budget,
        "owned_objects_count": sum(1 for obj in _session_objects(session) if obj.is_active)
        + _count_lot_objects(extra_portfolio_lots),
    }


def _asset_role(category: str, economic_role: str) -> str:
    role = _norm(economic_role)
    if role in {"consumer", "generator", "storage", "infrastructure", "mixed"}:
        return role
    category_norm = _norm(category)
    if category_norm in {"consumer", "generator", "storage", "infrastructure"}:
        return category_norm
    return "mixed"


def _collect_lot_assets(lots: Sequence[Lot] | None = None) -> List[Asset]:
    assets: List[Asset] = []
    for lot in lots or []:
        for item in _lot_items(lot):
            if item.object_type is None:
                continue
            params = dict(item.object_type.default_parameters_json or {})
            params.update(dict(item.overrides_json or {}))
            params["forecast_profile_key"] = item.object_type.forecast_profile_key or params.get(
                "forecast_profile_key", ""
            )
            params["resource_dependencies"] = list(item.object_type.resource_dependencies_json or [])
            params.setdefault("qty", max(1, int(item.quantity or 1)))
            role = _asset_role(item.object_type.category, item.object_type.economic_role)
            assets.append(
                Asset(
                    role=role,
                    code=item.object_type.code,
                    quantity=max(1, int(item.quantity or 1)),
                    parameters=params,
                )
            )
    return assets


def _simulate_assets(
    *,
    assets: List[Asset],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    ticks: List[int],
    cfg: Dict[str, Any],
) -> Dict[str, ScenarioSnapshot]:
    return {
        "base": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="base"
        ),
        "worst": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="worst"
        ),
        "best": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="best"
        ),
    }


def _lot_role_profile(lots: Sequence[Lot]) -> Dict[str, Any]:
    role_counts = {
        "consumer": 0,
        "generator": 0,
        "storage": 0,
        "infrastructure": 0,
        "mixed": 0,
    }
    total_units = 0
    for asset in _collect_lot_assets(lots):
        qty = max(1, int(asset.quantity or 1))
        role = asset.role if asset.role in role_counts else "mixed"
        role_counts[role] += qty
        total_units += qty
    dominant_role = max(role_counts.items(), key=lambda row: row[1])[0] if total_units else "mixed"
    if sum(1 for value in role_counts.values() if value > 0) > 1:
        dominant_role = "mixed"
    role_multipliers = {
        "consumer": {"target": 0.92, "cautious": 1.05, "ceiling": 0.88},
        "generator": {"target": 1.08, "cautious": 1.00, "ceiling": 1.06},
        "storage": {"target": 0.98, "cautious": 1.10, "ceiling": 0.94},
        "infrastructure": {"target": 0.86, "cautious": 1.15, "ceiling": 0.80},
        "mixed": {"target": 1.00, "cautious": 1.00, "ceiling": 1.00},
    }
    return {
        "counts": role_counts,
        "dominant_role": dominant_role,
        "total_units": total_units,
        "multipliers": role_multipliers[dominant_role],
    }


def _lot_connection_outlook(
    *,
    session: GameSession,
    lots: Sequence[Lot],
) -> Dict[str, Any]:
    rules_cfg = dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {})
    auction_cfg = _auction_bid_cfg(rules_cfg)
    topology_issues = validate_session_network(list(_session_objects(session)))
    topology_errors = [issue for issue in topology_issues if str(issue.severity or "") == "error"]
    topology_invalid = bool(topology_errors)
    topology_blocks_valuation = bool(
        auction_cfg.get("block_on_topology_invalid", DEFAULT_AUCTION_BID_CFG["block_on_topology_invalid"])
    )
    sector_agnostic = _ignore_connection_sectors(session)

    if sector_agnostic:
        items: List[Dict[str, Any]] = []
        total_items = 0
        recommended_points: set[str] = set()
        for lot in lots:
            for item in _lot_items(lot):
                if item.object_type is None:
                    continue
                qty = max(1, int(item.quantity or 1))
                total_items += qty
                items.append(
                    {
                        "lot_id": int(lot.id),
                        "lot_name": lot.name,
                        "object_type_code": item.object_type.code,
                        "object_type_name": item.object_type.name,
                        "category": item.object_type.category,
                        "quantity": qty,
                        "message": "Оценка лота выполняется без учёта секторов подключения.",
                        "is_feasible": not topology_invalid,
                        "current_point": None,
                        "recommended_point": None,
                        "recommended_loss_pct": 0.0,
                        "remaining_capacity_mw": 0.0,
                        "feasible_alternatives": [],
                        "rejected_points": [],
                    }
                )

        if topology_invalid and topology_blocks_valuation:
            blocked_items_count = max(len(topology_errors), total_items)
            status = "blocked"
            message = (
                "Сетевая топология сессии некорректна: "
                + "; ".join(str(issue.message) for issue in topology_errors[:3])
            )
            system_fit_score = float(-50.0 - len(topology_errors) * 10.0)
        elif topology_invalid:
            blocked_items_count = 0
            status = "advisory"
            message = (
                "В энергосистеме есть топологические ошибки, но аукционная оценка лота "
                "не блокируется: проблемы учтены как post-purchase advisory."
            )
            system_fit_score = float(-8.0 - len(topology_errors) * 2.0)
        else:
            blocked_items_count = 0
            status = "neutral"
            message = (
                "Оценка выполняется без учёта секторов подключения "
                "(A/B/C/...)."
            )
            system_fit_score = 0.0
        connection_block_reasons_count = len(topology_errors) if topology_invalid else 0
        feasible_items_count = max(0, total_items - blocked_items_count)
        return {
            "status": status,
            "message": message,
            "items": items,
            "estimated_delta_total": 0.0,
            "blocked_items_count": int(blocked_items_count),
            "feasible_items_count": int(feasible_items_count),
            "avg_recommended_loss_pct": 0.0,
            "system_fit_score": float(system_fit_score),
            "topology_invalid": bool(topology_invalid),
            "topology_blocks_valuation": bool(topology_blocks_valuation),
            "topology_issues": [issue.to_dict() for issue in topology_issues],
            "recommended_points": sorted(recommended_points),
            "connection_block_reasons_count": int(connection_block_reasons_count),
        }

    if topology_invalid and not topology_blocks_valuation:
        advisory_items: List[Dict[str, Any]] = []
        for lot in lots:
            for item in _lot_items(lot):
                if item.object_type is None:
                    continue
                qty = max(1, int(item.quantity or 1))
                advisory_items.append(
                    {
                        "lot_id": int(lot.id),
                        "lot_name": lot.name,
                        "object_type_code": item.object_type.code,
                        "object_type_name": item.object_type.name,
                        "category": item.object_type.category,
                        "quantity": qty,
                        "message": (
                            "Интеграция будет нужна после покупки; на этапе аукциона "
                            "оценка не требует ручного подключения."
                        ),
                        "is_feasible": True,
                        "current_point": None,
                        "recommended_point": None,
                        "recommended_loss_pct": 0.0,
                        "remaining_capacity_mw": 0.0,
                        "feasible_alternatives": [],
                        "rejected_points": [],
                    }
                )
        return {
            "status": "advisory",
            "message": (
                "Сетевая топология требует исправления, но аукционная оценка лотов выполняется "
                "без блокировки по топологии."
            ),
            "items": advisory_items,
            "estimated_delta_total": 0.0,
            "blocked_items_count": 0,
            "feasible_items_count": int(sum(int(item["quantity"]) for item in advisory_items)),
            "avg_recommended_loss_pct": 0.0,
            "system_fit_score": float(-10.0 - len(topology_errors) * 2.0),
            "topology_invalid": True,
            "topology_blocks_valuation": False,
            "topology_issues": [issue.to_dict() for issue in topology_issues],
            "recommended_points": [],
            "connection_block_reasons_count": int(len(topology_errors)),
        }

    items: List[Dict[str, Any]] = []
    estimated_delta_total = 0.0
    blocked_items_count = 0
    feasible_items_count = 0
    weighted_loss_total = 0.0
    weight_total = 0.0
    recommended_points: set[str] = set()
    connection_block_reasons_count = 0

    for lot in lots:
        for item in _lot_items(lot):
            if item.object_type is None:
                continue
            qty = max(1, int(item.quantity or 1))
            params = dict(item.object_type.default_parameters_json or {})
            params.update(dict(item.overrides_json or {}))
            params.setdefault("qty", qty)
            rec = recommend_connection_for_profile(
                session=session,
                object_type=item.object_type,
                parameters=params,
                district=params.get("district"),
                existing_objects=list(session.objects),
            )
            estimated_delta = float(rec.get("estimated_delta", 0.0) or 0.0) * qty
            ranked_points = list(rec.get("ranked_points") or [])
            feasible_alternatives = list(rec.get("feasible_alternatives") or [])
            rejected_points = list(rec.get("rejected_points") or [])
            recommended_loss_pct = float(rec.get("recommended_loss_pct", 0.0) or 0.0)
            feasible = bool(ranked_points) and math.isfinite(float(rec.get("recommended_score", 0.0)))
            recommended_point = str(rec.get("recommended_point") or "").strip()
            if recommended_point:
                recommended_points.add(recommended_point)
            if feasible:
                feasible_items_count += qty
            else:
                blocked_items_count += qty
            connection_block_reasons_count += int(len(rejected_points))
            if not feasible and not rejected_points:
                connection_block_reasons_count += 1
            estimated_delta_total += estimated_delta
            weighted_loss_total += recommended_loss_pct * qty
            weight_total += qty
            items.append(
                {
                    "lot_id": int(lot.id),
                    "lot_name": lot.name,
                    "object_type_code": item.object_type.code,
                    "object_type_name": item.object_type.name,
                    "category": item.object_type.category,
                    "quantity": qty,
                    "current_point": rec.get("current_point"),
                    "recommended_point": rec.get("recommended_point"),
                    "recommended_loss_pct": recommended_loss_pct,
                    "remaining_capacity_mw": float(
                        rec.get("recommended_remaining_capacity_mw", 0.0) or 0.0
                    ),
                    "estimated_delta": float(estimated_delta),
                    "profile_key": rec.get("profile_key") or item.object_type.forecast_profile_key or "",
                    "resource_dependencies": list(rec.get("resource_dependencies") or []),
                    "forecast_model_type": rec.get("forecast_model_type") or "",
                    "message": rec.get("message") or "",
                    "is_feasible": feasible,
                    "feasible_alternatives": feasible_alternatives,
                    "rejected_points": rejected_points,
                }
            )

    avg_loss_pct = float(weighted_loss_total / weight_total) if weight_total > 0 else 0.0
    system_fit_score = float(
        estimated_delta_total
        - blocked_items_count * 6.0
        - max(0.0, avg_loss_pct - 8.0) * max(1.0, weight_total) * 0.35
    )
    if topology_invalid and topology_blocks_valuation:
        blocked_items_count = max(blocked_items_count, len(topology_errors))
        system_fit_score -= 50.0 + len(topology_errors) * 10.0
        message = (
            "Сетевая топология сессии некорректна: "
            + "; ".join(str(issue.message) for issue in topology_errors[:3])
        )
        status = "blocked"
    elif topology_invalid:
        message = (
            "В энергосистеме есть топологические ошибки. На этапе аукциона это учитывается как "
            "post-purchase advisory и не блокирует оценку лота."
        )
        status = "advisory"
        system_fit_score -= 10.0 + len(topology_errors) * 2.0
    elif blocked_items_count > 0:
        message = (
            "Часть объектов лота не проходит по сетевым лимитам или точкам подключения. "
            "Это снижает рабочую цену."
        )
        status = "blocked"
    elif estimated_delta_total > 0.25:
        message = (
            "Подключение усиливает лот: найдены точки с меньшими потерями и достаточным запасом "
            "по мощности."
        )
        status = "supported"
    elif estimated_delta_total < -0.25:
        message = (
            "Сетевой fit слабый: даже лучшая точка подключения даёт мало экономической отдачи."
        )
        status = "risky"
    else:
        message = "Подключение нейтрально: сетевые ограничения не критичны, но явного бонуса нет."
        status = "neutral"
    return {
        "status": status,
        "message": message,
        "items": items,
        "estimated_delta_total": float(estimated_delta_total),
        "blocked_items_count": int(blocked_items_count),
        "feasible_items_count": int(feasible_items_count),
        "avg_recommended_loss_pct": float(avg_loss_pct),
        "system_fit_score": float(system_fit_score),
        "topology_invalid": bool(topology_invalid),
        "topology_blocks_valuation": bool(topology_blocks_valuation),
        "topology_issues": [issue.to_dict() for issue in topology_issues],
        "recommended_points": sorted(recommended_points),
        "connection_block_reasons_count": int(connection_block_reasons_count),
    }


def _period_series(
    periods: Sequence[ForecastPeriod],
) -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    ticks: List[int] = []
    profile_alias = {
        "house": "house_load",
        "housea": "house_load",
        "houseb": "house_load",
        "load_housea": "house_load",
        "load_houseb": "house_load",
        "consumption_houseb": "house_load",
        "office": "office_load",
        "load_office": "office_load",
        "factory": "factory_load",
        "load_factory": "factory_load",
    }
    for period in periods:
        tick = int(period.tick)
        ticks.append(tick)
        factor_rows = dict(period.factors_json or {})
        profile_rows = dict(period.profiles_json or {})

        if not factor_rows:
            if period.wind is not None:
                factor_rows["wind_factor"] = float(period.wind)
            if period.illumination is not None:
                factor_rows["solar_factor"] = float(period.illumination)
            if period.market_price is not None:
                factor_rows["market_price_buy"] = float(period.market_price)

        if not profile_rows:
            for raw_key, raw_value in (period.consumption_json or {}).items():
                key = profile_alias.get(_norm(raw_key))
                if key is None:
                    continue
                profile_rows[key] = _as_float(raw_value, 0.0)

        for key, value in factor_rows.items():
            factors.setdefault(_norm(key), {})[tick] = _as_float(value, 0.0)
        for key, value in profile_rows.items():
            profiles.setdefault(_norm(key), {})[tick] = _as_float(value, 0.0)
    return factors, profiles, sorted(set(ticks))


def _bundled_series() -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    pack = load_bundled_forecast_pack()
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    wind = (pack.get("wind", {}) or {}).get("wind", {}) or {}
    solar = (pack.get("solar", {}) or {}).get("solar", {}) or {}
    market = (pack.get("market", {}) or {}).get("price", {}) or {}
    if wind:
        factors["wind_factor"] = {int(k): _as_float(v, 0.0) for k, v in wind.items()}
    if solar:
        factors["solar_factor"] = {int(k): _as_float(v, 0.0) for k, v in solar.items()}
    if market:
        factors["market_price_buy"] = {int(k): _as_float(v, 0.0) for k, v in market.items()}

    load = pack.get("load", {}) or {}
    for raw_key, values in load.items():
        key = _norm(raw_key)
        if key in {"house", "housea", "houseb", "load_housea", "load_houseb"}:
            canonical = "house_load"
        elif key in {"office", "load_office"}:
            canonical = "office_load"
        elif key in {"factory", "load_factory"}:
            canonical = "factory_load"
        else:
            continue
        target = profiles.setdefault(canonical, {})
        for tick, value in (values or {}).items():
            target[int(tick)] = _as_float(value, 0.0)

    ticks = sorted(
        {
            *{int(tick) for rows in factors.values() for tick in rows.keys()},
            *{int(tick) for rows in profiles.values() for tick in rows.keys()},
        }
    )
    return factors, profiles, ticks


def _scenario_multipliers(cfg: Dict[str, Any], scenario: str) -> Dict[str, float]:
    scen_cfg = dict(cfg.get("scenarios", {}) or {})
    item = dict(scen_cfg.get(scenario, {}) or {})
    if item:
        return {
            "wind": _as_float(item.get("wind"), 1.0),
            "solar": _as_float(item.get("solar"), 1.0),
            "load": _as_float(item.get("load"), 1.0),
        }
    if scenario == "worst":
        return {"wind": 0.9, "solar": 0.8, "load": 1.1}
    if scenario == "best":
        return {"wind": 1.1, "solar": 1.2, "load": 0.9}
    return {"wind": 1.0, "solar": 1.0, "load": 1.0}


def _profile_value(
    profile_key: str, profiles: Dict[str, Dict[int, float]], tick: int, default: float = 1.0
) -> float:
    series = profiles.get(_norm(profile_key)) or {}
    if tick in series:
        return _as_float(series[tick], default)
    return float(default)


def _factor_value(
    factor_key: str, factors: Dict[str, Dict[int, float]], tick: int, default: float = 0.0
) -> float:
    series = factors.get(_norm(factor_key)) or {}
    if tick in series:
        return _as_float(series[tick], default)
    return float(default)


def _consumer_demand_mw(
    *,
    expected_consumption_mw: float,
    profile_value: float,
    load_scale: float,
) -> float:
    # Canonical forecast rows may come either as factors (0..1.5) or absolute MW (>1.5).
    if profile_value <= 1.5:
        demand = max(0.0, expected_consumption_mw) * max(0.0, profile_value)
    else:
        demand = max(0.0, profile_value)
    return max(0.0, demand * max(0.0, load_scale))


def _wind_generation_mw(
    *,
    wind_value: float,
    generation_mw: float,
    efficiency: float,
    object_defaults: Dict[str, Any],
) -> float:
    speed_or_factor = max(0.0, wind_value)
    cap = max(0.0, generation_mw)
    eff = _clamp(efficiency, 0.1, 1.2)
    if speed_or_factor <= 1.5:
        return cap * speed_or_factor * eff
    k = max(0.0, _as_float(object_defaults.get("wind_k_default"), 0.08))
    physical = k * (speed_or_factor**3)
    return min(cap, physical) * eff


def _solar_generation_mw(
    *,
    solar_value: float,
    generation_mw: float,
    efficiency: float,
) -> float:
    irradiation_or_factor = max(0.0, solar_value)
    cap = max(0.0, generation_mw)
    eff = _clamp(efficiency, 0.1, 1.2)
    if irradiation_or_factor <= 1.5:
        return cap * irradiation_or_factor * eff
    return min(cap, irradiation_or_factor) * eff


def _asset_profile_key(asset: Asset) -> str:
    explicit = _norm(asset.parameters.get("forecast_profile_key", ""))
    if explicit:
        return explicit
    fallback = _norm(asset.parameters.get("profile", ""))
    if fallback in {"house", "housea", "load_housea"}:
        return "house_load"
    if fallback in {"office", "load_office"}:
        return "office_load"
    if fallback in {"factory", "load_factory"}:
        return "factory_load"
    if fallback:
        return fallback
    if asset.role == "consumer":
        if _norm(asset.code) in {"factory"}:
            return "factory_load"
        if _norm(asset.code) in {"office"}:
            return "office_load"
        return "house_load"
    if asset.role == "generator" and _norm(asset.code) in {"wind"}:
        return "wind_profile"
    if asset.role == "generator" and _norm(asset.code) in {"solar", "cyber_solar", "solarrobot"}:
        return "solar_profile"
    if asset.role == "storage":
        return "storage_default_profile"
    return ""


def _collect_assets(session: GameSession, extra_lots: Sequence[Lot] | None = None) -> List[Asset]:
    assets: List[Asset] = []
    for obj in _session_objects(session):
        if not obj.is_active or obj.object_type is None:
            continue
        params = dict(obj.object_type.default_parameters_json or {})
        params.update(dict(obj.current_parameters_json or {}))
        params.setdefault("district", obj.district)
        params["forecast_profile_key"] = obj.object_type.forecast_profile_key or params.get(
            "forecast_profile_key", ""
        )
        params["resource_dependencies"] = list(obj.object_type.resource_dependencies_json or [])
        role = _asset_role(obj.object_type.category, obj.object_type.economic_role)
        assets.append(
            Asset(
                role=role,
                code=obj.object_type.code,
                quantity=max(1, int(params.get("qty", 1) or 1)),
                parameters=params,
            )
        )
    assets.extend(_collect_lot_assets(extra_lots))
    return assets


def _simulate_scenario(
    *,
    assets: List[Asset],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    ticks: List[int],
    cfg: Dict[str, Any],
    scenario: str,
) -> ScenarioSnapshot:
    scales = _scenario_multipliers(cfg, scenario)
    market_cfg = dict(cfg.get("market", {}) or {})
    fine_cfg = dict(cfg.get("fine", {}) or {})
    net_cfg = dict(cfg.get("network", {}) or {})
    sector_agnostic = _ignore_connection_sectors_cfg(cfg)
    eco_cfg = dict(cfg.get("eco", {}) or {})
    storage_cfg = dict(cfg.get("storage", {}) or {})
    evaluation_cfg = dict(cfg.get("evaluation", {}) or {})
    object_defaults = dict(cfg.get("object_defaults", {}) or {})

    market_buy_default = _as_float(market_cfg.get("external_buy_price"), 10.0)
    market_sell_default = _as_float(market_cfg.get("external_sell_price"), 2.0)
    penalty_rate = _as_float(fine_cfg.get("class3_rub_per_mw"), 10.0)
    factory_penalty_rate = _as_float(fine_cfg.get("factory_rub_per_mw"), 10.0)
    loss_tax = _as_float(net_cfg.get("loss_tax"), 1.0)
    base_loss_rate = 0.05
    default_connection_point = str(net_cfg.get("default_connection_point") or "A").strip().upper()
    if sector_agnostic:
        default_connection_point = "ANY"
        point_loss_by_connection: Dict[str, float] = {default_connection_point: 0.0}
        default_point_loss = 0.0
    else:
        point_losses_raw = dict(net_cfg.get("connection_loss_pct_by_point") or {})
        point_loss_by_connection = {
            str(key).strip().upper(): max(0.0, _as_float(value, 0.0)) / 100.0
            for key, value in point_losses_raw.items()
            if str(key).strip()
        }
        default_point_loss = float(point_loss_by_connection.get(default_connection_point, 0.0))
    storage_throughput_cost = max(
        0.0,
        _as_float(
            evaluation_cfg.get("storage_operating_cost_per_mwh_throughput"),
            _as_float(storage_cfg.get("operating_cost_per_mwh_throughput"), 0.0),
        ),
    )

    storage_capacity = max(0.0, _as_float(storage_cfg.get("capacity_mw_tick"), 20.0))
    storage_charge = max(0.0, _as_float(storage_cfg.get("charge_rate_mw"), 5.0))
    storage_discharge = max(0.0, _as_float(storage_cfg.get("discharge_rate_mw"), 5.0))
    storage_leak = _clamp(_as_float(storage_cfg.get("leak_fraction_per_tick"), 0.0), 0.0, 0.3)

    market_buy_cap_raw = market_cfg.get("market_max_power")
    if market_buy_cap_raw in (None, ""):
        market_buy_cap_raw = market_cfg.get("instant_buy_max_power")
    market_buy_capacity = (
        float("inf")
        if market_buy_cap_raw in (None, "", 0, 0.0)
        else max(0.0, _as_float(market_buy_cap_raw, 0.0))
    )

    market_sell_cap_raw = market_cfg.get("instant_sell_max_power")
    market_sell_capacity = (
        float("inf")
        if market_sell_cap_raw in (None, "", 0, 0.0)
        else max(0.0, _as_float(market_sell_cap_raw, 0.0))
    )

    point_capacity_raw = dict(net_cfg.get("connection_capacity_mw_by_point") or {})
    line_capacity_default = max(0.0, _as_float(net_cfg.get("line_max_power_mw"), 0.0))

    def asset_connection_point(asset: Asset) -> str:
        if sector_agnostic:
            return default_connection_point
        explicit_point = str(
            asset.parameters.get("connection_point")
            or asset.parameters.get("point")
            or asset.parameters.get("cell")
            or asset.parameters.get("slot")
            or ""
        ).strip().upper()
        if explicit_point:
            return explicit_point
        return default_connection_point

    def asset_connection_loss(asset: Asset) -> float:
        point = asset_connection_point(asset)
        return float(point_loss_by_connection.get(point, default_point_loss))

    infrastructure_support_by_point: Dict[str, Dict[str, float]] = {}
    infrastructure_capacity_bonus_total = 0.0
    infrastructure_loss_reduction_total = 0.0
    for asset in assets:
        if asset.role != "infrastructure":
            continue
        qty = max(1, int(asset.quantity or 1))
        point = asset_connection_point(asset)
        ports = max(0.0, _as_float(asset.parameters.get("ports"), 0.0)) * qty
        soft_flow = max(0.0, _as_float(asset.parameters.get("soft_flow_limit_mw"), 0.0)) * qty
        wear = max(0.0, _as_float(asset.parameters.get("wear_impact"), 0.0)) * qty
        capacity_bonus = max(0.0, soft_flow * 0.25 + ports * 1.5 - wear)
        loss_reduction = min(0.04, ports * 0.004 + soft_flow * 0.0005)
        bucket = infrastructure_support_by_point.setdefault(
            point,
            {"capacity_bonus_mw": 0.0, "loss_reduction": 0.0},
        )
        bucket["capacity_bonus_mw"] += capacity_bonus
        bucket["loss_reduction"] += loss_reduction
        infrastructure_capacity_bonus_total += capacity_bonus
        infrastructure_loss_reduction_total += loss_reduction

    point_capacity_by_connection: Dict[str, float] = {}
    if sector_agnostic:
        fallback_candidates = [max(0.0, line_capacity_default)]
        fallback_candidates.extend(
            max(0.0, _as_float(value, 0.0)) for value in point_capacity_raw.values()
        )
        shared_capacity = max(fallback_candidates) if fallback_candidates else 0.0
        support_bonus = max(
            0.0,
            _as_float(
                (infrastructure_support_by_point.get(default_connection_point) or {}).get(
                    "capacity_bonus_mw"
                ),
                0.0,
            ),
        )
        capacity = shared_capacity + support_bonus
        if capacity <= 0.0:
            capacity = float("inf")
        point_capacity_by_connection[default_connection_point] = float(capacity)
    else:
        for point in set(
            [
                *point_loss_by_connection.keys(),
                *point_capacity_raw.keys(),
                *(asset_connection_point(asset) for asset in assets),
                default_connection_point,
            ]
        ):
            base_capacity = max(0.0, _as_float(point_capacity_raw.get(point), line_capacity_default))
            support_bonus = max(
                0.0,
                _as_float(
                    (infrastructure_support_by_point.get(point) or {}).get("capacity_bonus_mw"),
                    0.0,
                ),
            )
            capacity = base_capacity + support_bonus
            if capacity <= 0.0:
                capacity = float("inf")
            point_capacity_by_connection[point] = float(capacity)

    all_connection_losses = [asset_connection_loss(asset) for asset in assets] or [default_point_loss]
    avg_connection_loss = float(sum(all_connection_losses) / len(all_connection_losses))
    network_loss_rate = max(
        0.0,
        base_loss_rate
        + avg_connection_loss * 0.5
        - min(0.03, infrastructure_loss_reduction_total * 0.5),
    )

    contracts = sum(
        _as_float(asset.parameters.get("contract_rub_per_tick"), 0.0) * asset.quantity
        for asset in assets
    ) * len(ticks)
    income_total = 0.0
    market_net = 0.0
    fuel_and_taxes = 0.0
    penalties_total = 0.0
    losses_total = 0.0
    eco_value = 0.0
    risk_penalty = 0.0
    served_load_revenue = 0.0
    avoided_market_purchase_value = 0.0
    export_revenue = 0.0
    market_purchase_cost = 0.0
    storage_operating_cost = 0.0
    overload_penalties = 0.0
    deficit_penalties = 0.0

    role_breakdown = {
        "consumer": 0.0,
        "generator": 0.0,
        "storage": 0.0,
        "infrastructure": 0.0,
        "mixed": 0.0,
    }
    storage_state = 0.0
    storage_max = 0.0
    for asset in assets:
        if asset.role != "storage":
            continue
        storage_max += (
            max(0.0, _as_float(asset.parameters.get("capacity_mw_tick"), storage_capacity))
            * asset.quantity
        )
    storage_state = 0.0

    for tick in ticks:
        wind_factor = _factor_value("wind_factor", factors, tick, 0.0) * scales["wind"]
        solar_factor = _factor_value("solar_factor", factors, tick, 0.0) * scales["solar"]
        market_buy = _factor_value("market_price_buy", factors, tick, market_buy_default)
        market_sell = _factor_value(
            "market_price_sell",
            factors,
            tick,
            market_sell_default if market_sell_default > 0 else market_buy * 0.5,
        )
        fuel_price = _factor_value(
            "fuel_price", factors, tick, _as_float(cfg.get("tps", {}).get("fuel_price"), 0.5)
        )

        demand_total = 0.0
        demand_factory = 0.0
        renewable_generation = 0.0
        thermal_units: List[Dict[str, Any]] = []
        point_usage_mw: Dict[str, float] = {}

        for asset in assets:
            qty = max(1, int(asset.quantity))
            profile_key = _asset_profile_key(asset)
            profile_value = _profile_value(profile_key, profiles, tick, 1.0)
            point = asset_connection_point(asset)
            if asset.role == "consumer":
                base_load = _as_float(asset.parameters.get("expected_consumption_mw"), 1.0)
                connection_loss = asset_connection_loss(asset)
                load = (
                    _consumer_demand_mw(
                        expected_consumption_mw=base_load,
                        profile_value=profile_value,
                        load_scale=scales["load"],
                    )
                    * qty
                    * (1.0 + connection_loss)
                )
                demand_total += load
                point_usage_mw[point] = point_usage_mw.get(point, 0.0) + load
                if _norm(asset.code) == "factory":
                    demand_factory += load
            elif asset.role == "generator":
                code = _norm(asset.code)
                eff = _clamp(_as_float(asset.parameters.get("efficiency"), 1.0), 0.1, 1.2)
                delivery_factor = max(0.0, 1.0 - asset_connection_loss(asset))
                generation_mw = (
                    max(0.0, _as_float(asset.parameters.get("generation_mw"), 0.0)) * qty
                )
                if code in {"wind"}:
                    wind_input = max(0.0, wind_factor) * max(0.0, profile_value)
                    supply = _wind_generation_mw(
                        wind_value=wind_input,
                        generation_mw=generation_mw,
                        efficiency=eff,
                        object_defaults=object_defaults,
                    )
                    supply *= delivery_factor
                    renewable_generation += supply
                    point_usage_mw[point] = point_usage_mw.get(point, 0.0) + supply
                    eco_value += (
                        supply
                        * _as_float(eco_cfg.get("wind_points_per_mw_tick"), 1.0)
                        * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
                    )
                elif code in {"solar", "cyber_solar", "solarrobot"}:
                    solar_input = max(0.0, solar_factor) * max(0.0, profile_value)
                    supply = _solar_generation_mw(
                        solar_value=solar_input,
                        generation_mw=generation_mw,
                        efficiency=eff,
                    )
                    supply *= delivery_factor
                    renewable_generation += supply
                    point_usage_mw[point] = point_usage_mw.get(point, 0.0) + supply
                    eco_value += (
                        supply
                        * _as_float(eco_cfg.get("solar_points_per_mw_tick"), 1.0)
                        * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
                    )
                else:
                    thermal_units.append(
                        {
                            "point": point,
                            "capacity": generation_mw * delivery_factor,
                            "eta": _clamp(
                                _as_float(
                                    asset.parameters.get("efficiency"),
                                    _as_float(cfg.get("tps", {}).get("eta_nominal"), 0.9),
                                ),
                                0.2,
                                1.0,
                            ),
                            "tax": _as_float(
                                asset.parameters.get("eco_tax_fuel"),
                                _as_float(cfg.get("tps", {}).get("eco_tax_fuel"), 0.0),
                            ),
                        }
                    )
            elif asset.role == "infrastructure":
                continue

        if storage_state > 0 and storage_leak > 0:
            storage_state = max(0.0, storage_state * (1.0 - storage_leak))

        losses_mw = max(0.0, renewable_generation) * network_loss_rate
        losses_total += losses_mw * loss_tax
        renewable_available = max(0.0, renewable_generation - losses_mw)
        renewable_to_load = min(demand_total, renewable_available)
        renewable_surplus = max(0.0, renewable_available - renewable_to_load)
        deficit = max(0.0, demand_total - renewable_to_load)

        if renewable_to_load > 0:
            avoided_market_purchase_value += renewable_to_load * market_buy
            role_breakdown["generator"] += renewable_to_load * market_buy

        storage_charge_total = 0.0
        storage_discharge_total = 0.0
        if deficit > 0 and storage_state > 0 and storage_max > 0:
            discharge_cap = storage_discharge * max(1.0, storage_max / max(storage_capacity, 1.0))
            discharge = min(deficit, storage_state, discharge_cap)
            storage_state -= discharge
            storage_discharge_total += discharge
            deficit = max(0.0, deficit - discharge)
            point_usage_mw[default_connection_point] = (
                point_usage_mw.get(default_connection_point, 0.0) + discharge
            )
            avoided_market_purchase_value += discharge * market_buy
            role_breakdown["storage"] += discharge * market_buy
            eco_value += (
                discharge
                * _as_float(eco_cfg.get("storage_discharge_points_per_mw_tick"), 0.0)
                * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
            )

        for unit in thermal_units:
            if deficit <= 0:
                break
            variable_cost = fuel_price / max(0.2, unit["eta"]) + unit["tax"]
            if variable_cost > market_buy:
                continue
            dispatch = min(deficit, unit["capacity"])
            if dispatch <= 0:
                continue
            deficit -= dispatch
            fuel_and_taxes += dispatch * variable_cost
            point_usage_mw[str(unit.get("point") or default_connection_point)] = (
                point_usage_mw.get(str(unit.get("point") or default_connection_point), 0.0) + dispatch
            )
            avoided_market_purchase_value += dispatch * market_buy
            role_breakdown["generator"] += dispatch * market_buy - dispatch * variable_cost

        market_purchase = 0.0
        if deficit > 0:
            market_purchase = min(deficit, market_buy_capacity)
            market_purchase_cost += market_purchase * market_buy
            market_net += market_purchase * market_buy
            deficit = max(0.0, deficit - market_purchase)

        if deficit > 0:
            deficit_penalties += max(0.0, deficit - 1.0) * penalty_rate
            if demand_factory > 0 and demand_total > 0:
                deficit_penalties += (
                    demand_factory * (deficit / demand_total) * factory_penalty_rate
                )

        for point, used_mw in point_usage_mw.items():
            capacity = float(point_capacity_by_connection.get(point, float("inf")))
            if math.isfinite(capacity) and used_mw > capacity:
                overload = max(0.0, used_mw - capacity)
                overload_penalties += overload * market_buy * 0.60
                risk_penalty += overload * market_buy * 0.30
                role_breakdown["infrastructure"] += min(
                    overload * market_buy * 0.20,
                    infrastructure_capacity_bonus_total * market_buy * 0.05,
                )

        risk_penalty += (
            max(0.0, losses_mw * market_buy * 0.08)
            + max(0.0, deficit) * (penalty_rate + factory_penalty_rate) * 0.5
            + max(0.0, avg_connection_loss)
            * max(demand_total, renewable_generation)
            * market_buy
            * 0.03
        )
        infra_relief = min(
            risk_penalty,
            (infrastructure_capacity_bonus_total * 0.01 + infrastructure_loss_reduction_total * 10.0)
            * market_buy
            * 0.10,
        )
        risk_penalty = max(0.0, risk_penalty - infra_relief)
        role_breakdown["infrastructure"] += float(max(0.0, infra_relief))

        surplus = renewable_surplus
        if surplus > 0 and storage_max > 0 and storage_state < storage_max:
            charge_cap = storage_charge * max(1.0, storage_max / max(storage_capacity, 1.0))
            charge = min(surplus, storage_max - storage_state, charge_cap)
            storage_state += charge
            surplus -= charge
            storage_charge_total += charge
            point_usage_mw[default_connection_point] = (
                point_usage_mw.get(default_connection_point, 0.0) + charge
            )

        if surplus > 0:
            exported = min(surplus, market_sell_capacity)
            if exported > 0:
                export_revenue += exported * market_sell
                market_net -= exported * market_sell
                role_breakdown["generator"] += exported * market_sell

        served_ratio = 1.0
        if demand_total > 1e-9:
            served_ratio = _clamp((demand_total - deficit) / demand_total, 0.0, 1.0)
        for asset in assets:
            if asset.role != "consumer":
                continue
            qty = max(1, int(asset.quantity))
            base_load = _as_float(asset.parameters.get("expected_consumption_mw"), 1.0)
            profile_key = _asset_profile_key(asset)
            profile_value = _profile_value(profile_key, profiles, tick, 1.0)
            connection_loss = asset_connection_loss(asset)
            demand = (
                _consumer_demand_mw(
                    expected_consumption_mw=base_load,
                    profile_value=profile_value,
                    load_scale=scales["load"],
                )
                * qty
                * (1.0 + connection_loss)
            )
            served = demand * served_ratio
            tariff = _as_float(asset.parameters.get("tariff_rub_per_mw_tick"), 0.0)
            served_load_revenue += served * tariff
            role_breakdown["consumer"] += served * tariff

        if storage_charge_total > 0 or storage_discharge_total > 0:
            storage_throughput = storage_charge_total + storage_discharge_total
            storage_operating_cost += storage_throughput * storage_throughput_cost
            storage_cycle_value = (
                role_breakdown["storage"] - storage_throughput * storage_throughput_cost
            )
            role_breakdown["storage"] = max(0.0, storage_cycle_value)

    penalties_total = overload_penalties + deficit_penalties
    income_total = served_load_revenue + export_revenue + max(0.0, eco_value)
    cost_total = contracts + fuel_and_taxes + storage_operating_cost + max(0.0, market_net)
    net_profit = income_total - cost_total - penalties_total - losses_total
    utility_score = net_profit - risk_penalty

    return ScenarioSnapshot(
        income_total=float(income_total),
        generation_income=float(export_revenue),
        cost_total=float(cost_total),
        contracts=float(contracts),
        fuel_and_taxes=float(fuel_and_taxes),
        market_net=float(market_net),
        penalties_total=float(penalties_total),
        losses_total=float(losses_total),
        risk_penalty=float(risk_penalty),
        flexibility_value=float(max(0.0, role_breakdown.get("storage", 0.0))),
        reserve_value=float(max(0.0, role_breakdown.get("infrastructure", 0.0))),
        eco_value=float(eco_value),
        net_profit=float(net_profit),
        utility_score=float(utility_score),
        served_load_revenue=float(served_load_revenue),
        avoided_market_purchase_value=float(avoided_market_purchase_value),
        export_revenue=float(export_revenue),
        market_purchase_cost=float(market_purchase_cost),
        storage_operating_cost=float(storage_operating_cost),
        overload_penalties=float(overload_penalties),
        deficit_penalties=float(deficit_penalties),
        role_breakdown={key: float(value) for key, value in role_breakdown.items()},
    )


def _to_delta(before: ScenarioSnapshot, after: ScenarioSnapshot) -> DeltaSnapshot:
    role_delta: Dict[str, float] = {}
    for role in set(before.role_breakdown.keys()) | set(after.role_breakdown.keys()):
        role_delta[role] = float(
            after.role_breakdown.get(role, 0.0) - before.role_breakdown.get(role, 0.0)
        )
    return DeltaSnapshot(
        delta_total=float(after.utility_score - before.utility_score),
        delta_income=float(after.income_total - before.income_total),
        delta_penalties=float(after.penalties_total - before.penalties_total),
        delta_contracts=float(after.contracts - before.contracts),
        delta_fuel_and_taxes=float(after.fuel_and_taxes - before.fuel_and_taxes),
        delta_market_net=float(after.market_net - before.market_net),
        delta_network_losses_cost=float(after.losses_total - before.losses_total),
        delta_eco_value=float(after.eco_value - before.eco_value),
        delta_risk_penalty=float(after.risk_penalty - before.risk_penalty),
        delta_eco_points=float((after.eco_value - before.eco_value) / 2.0),
        delta_served_load_revenue=float(after.served_load_revenue - before.served_load_revenue),
        delta_avoided_market_purchase_value=float(
            after.avoided_market_purchase_value - before.avoided_market_purchase_value
        ),
        delta_export_revenue=float(after.export_revenue - before.export_revenue),
        delta_market_purchase_cost=float(after.market_purchase_cost - before.market_purchase_cost),
        delta_storage_operating_cost=float(
            after.storage_operating_cost - before.storage_operating_cost
        ),
        delta_overload_penalties=float(after.overload_penalties - before.overload_penalties),
        delta_deficit_penalties=float(after.deficit_penalties - before.deficit_penalties),
        role_breakdown=role_delta,
    )


def _human_reasons(delta_obj: DeltaSnapshot, top_k: int = 5) -> List[str]:
    contrib = [
        ("Доход", delta_obj.delta_income),
        ("Штрафы", -delta_obj.delta_penalties),
        ("Контракты", -delta_obj.delta_contracts),
        ("Топливо и налоги", -delta_obj.delta_fuel_and_taxes),
        ("Рынок", -delta_obj.delta_market_net),
        ("Потери", -delta_obj.delta_network_losses_cost),
        ("Риск", -delta_obj.delta_risk_penalty),
        ("Эко-эффект", delta_obj.delta_eco_value),
    ]
    contrib.sort(key=lambda row: abs(row[1]), reverse=True)
    out: List[str] = []
    for key, value in contrib:
        if abs(value) < 1e-6:
            continue
        out.append(f"{key}: {value:+.2f}")
        if len(out) >= top_k:
            break
    return out


def _scenario_comment(
    *,
    scenario: str,
    utility_total: float,
    net_profit: float,
    recommended_bid: float,
    penalties_total: float,
    losses_total: float,
) -> str:
    scenario_key = _norm(scenario)
    if recommended_bid <= 0.0:
        if scenario_key == "worst":
            return (
                "Worst: сценарий уязвим, безопасная ставка отсутствует; "
                "дефицит и потери перекрывают эффект лота."
            )
        if scenario_key == "best":
            return (
                "Best: потенциал высокий, но при текущих параметрах риск всё ещё выше "
                "допустимого для ставки."
            )
        return "Base: при текущих вводных сценарий не поддерживает безопасную ставку."
    if scenario_key == "worst":
        return (
            "Worst: повышенная чувствительность к потерям и штрафам; "
            f"убытки по рискам {losses_total:.2f}, штрафы {penalties_total:.2f}."
        )
    if scenario_key == "best":
        return (
            "Best: выраженная синергия генерации и спроса, "
            "сетевые издержки компенсируются ростом маржи."
        )
    if utility_total >= 0 and net_profit >= 0:
        return "Base: рабочий нейтральный сценарий, лот поддерживает устойчивую доходность."
    if utility_total >= 0:
        return "Base: полезность положительная, но прибыль чувствительна к цене входа."
    return "Base: сценарий слабый, требуется более консервативная цена покупки."


def _scenario_row(
    *,
    label: str,
    scenario: str = "base",
    delta_obj: Any,
    current_price: float,
    pwin: float,
    remaining_budget: float,
) -> Dict[str, Any]:
    del pwin
    market_delta = float(getattr(delta_obj, "delta_market_net", 0.0) or 0.0)
    served_load_revenue = float(
        getattr(delta_obj, "delta_served_load_revenue", getattr(delta_obj, "delta_income", 0.0))
        or 0.0
    )
    export_revenue = float(
        getattr(delta_obj, "delta_export_revenue", max(0.0, -market_delta)) or 0.0
    )
    expenses_total = float(current_price) + max(
        0.0, float(getattr(delta_obj, "delta_contracts", 0.0) or 0.0)
    )
    expenses_total += max(0.0, float(getattr(delta_obj, "delta_fuel_and_taxes", 0.0) or 0.0))
    expenses_total += max(
        0.0,
        float(getattr(delta_obj, "delta_storage_operating_cost", 0.0) or 0.0),
    )
    expenses_total += max(
        0.0,
        float(getattr(delta_obj, "delta_market_purchase_cost", market_delta) or 0.0),
    )
    penalties_total = max(0.0, float(getattr(delta_obj, "delta_penalties", 0.0) or 0.0))
    losses_total = max(
        0.0,
        float(getattr(delta_obj, "delta_network_losses_cost", 0.0) or 0.0),
    ) + max(0.0, float(getattr(delta_obj, "delta_risk_penalty", 0.0) or 0.0))
    income_total = (
        max(0.0, served_load_revenue)
        + max(0.0, export_revenue)
        + max(0.0, float(getattr(delta_obj, "delta_eco_value", 0.0) or 0.0))
    )
    utility_total = float(delta_obj.delta_total)
    net_profit = income_total - expenses_total - penalties_total - losses_total
    gross_profit_before_bid = _gross_profit_before_bid(net_profit, current_price)
    return {
        "label": label,
        "revenue_total": float(income_total),
        "cost_total": float(expenses_total),
        "penalties_total": float(penalties_total),
        "losses_total": float(losses_total),
        "net_profit": float(net_profit),
        "gross_profit_before_bid": float(gross_profit_before_bid),
        "utility_score": float(utility_total),
        "bid_ceiling": 0.0,
        "recommended_bid": 0.0,
        "net_profit_at_recommended_bid": float(gross_profit_before_bid),
        "net_profit_at_bid_ceiling": float(gross_profit_before_bid),
        "remaining_budget_after_recommended_bid": max(0.0, float(remaining_budget)),
        "explanation": _scenario_comment(
            scenario=scenario,
            utility_total=float(utility_total),
            net_profit=float(net_profit),
            recommended_bid=0.0,
            penalties_total=float(penalties_total),
            losses_total=float(losses_total),
        ),
        # Backward-compatible aliases:
        "income_total": float(income_total),
        "expenses_total": float(expenses_total),
        "utility_total": float(utility_total),
        "expected_net_profit_at_current_price": float(net_profit),
        "comment": _scenario_comment(
            scenario=scenario,
            utility_total=float(utility_total),
            net_profit=float(net_profit),
            recommended_bid=0.0,
            penalties_total=float(penalties_total),
            losses_total=float(losses_total),
        ),
    }


def _populate_scenario_bid_metrics(
    *,
    row: Dict[str, Any],
    scenario: str,
    p_win: float,
    serious_competitors: int,
    cfg: Dict[str, Any],
    remaining_budget: float,
) -> Dict[str, Any]:
    payload = dict(row)
    scenario_conservative_utility = max(
        0.0,
        float(payload.get("utility_score", 0.0) or 0.0),
        float(payload.get("net_profit", 0.0) or 0.0),
    )
    bid_metrics = build_bid_recommendations(
        conservative_utility_value=scenario_conservative_utility,
        gross_profit_before_bid=float(payload.get("gross_profit_before_bid", 0.0) or 0.0),
        remaining_budget=remaining_budget,
        p_win=float(p_win),
        serious_competitors=int(serious_competitors),
        cfg=cfg,
    )
    payload.update(
        {
            "recommended_bid": float(bid_metrics["recommended_bid"]),
            "bid_ceiling": float(bid_metrics["hard_ceiling_bid"]),
            "net_profit_at_recommended_bid": float(
                bid_metrics["net_profit_at_recommended_bid"]
            ),
            "net_profit_at_bid_ceiling": float(
                _profit_after_bid(
                    float(payload.get("gross_profit_before_bid", 0.0) or 0.0),
                    float(bid_metrics["hard_ceiling_bid"]),
                )
            ),
            "remaining_budget_after_recommended_bid": float(
                bid_metrics["remaining_budget_after_recommended_bid"]
            ),
        }
    )
    explanation = _scenario_comment(
        scenario=scenario,
        utility_total=float(payload.get("utility_score", 0.0) or 0.0),
        net_profit=float(payload.get("net_profit", 0.0) or 0.0),
        recommended_bid=float(payload["recommended_bid"]),
        penalties_total=float(payload.get("penalties_total", 0.0) or 0.0),
        losses_total=float(payload.get("losses_total", 0.0) or 0.0),
    )
    payload["explanation"] = explanation
    payload["comment"] = explanation
    return payload


def _financial_breakdown(
    *,
    base_delta: Any,
    current_price: float,
    hard_bid: float,
    recommended_bid: float,
    max_bid: float,
    remaining_budget: float,
    model_risk_premium: float = 0.0,
) -> Dict[str, Any]:
    market_delta = float(getattr(base_delta, "delta_market_net", 0.0) or 0.0)
    served_load_revenue = float(
        getattr(base_delta, "delta_served_load_revenue", getattr(base_delta, "delta_income", 0.0))
        or 0.0
    )
    export_revenue = float(
        getattr(base_delta, "delta_export_revenue", max(0.0, -market_delta)) or 0.0
    )
    avoided_market_purchase_value = float(
        getattr(base_delta, "delta_avoided_market_purchase_value", 0.0) or 0.0
    )
    storage_operating_cost = float(getattr(base_delta, "delta_storage_operating_cost", 0.0) or 0.0)
    overload_penalties = float(getattr(base_delta, "delta_overload_penalties", 0.0) or 0.0)
    deficit_penalties = float(getattr(base_delta, "delta_deficit_penalties", 0.0) or 0.0)
    income = {
        "object_income": float(max(0.0, served_load_revenue)),
        "market_income": float(max(0.0, export_revenue)),
        "eco_value": float(max(0.0, float(getattr(base_delta, "delta_eco_value", 0.0) or 0.0))),
        "served_load_revenue": float(max(0.0, served_load_revenue)),
        "export_revenue": float(max(0.0, export_revenue)),
        "avoided_market_purchase_value": float(max(0.0, avoided_market_purchase_value)),
    }
    income["total"] = float(income["object_income"] + income["market_income"] + income["eco_value"])

    expenses = {
        "entry_price": float(current_price),
        "contract_costs": float(
            max(0.0, float(getattr(base_delta, "delta_contracts", 0.0) or 0.0))
        ),
        "fuel_and_taxes": float(
            max(0.0, float(getattr(base_delta, "delta_fuel_and_taxes", 0.0) or 0.0))
        ),
        "storage_operating_cost": float(max(0.0, storage_operating_cost)),
        "market_purchase": float(
            max(
                0.0,
                float(getattr(base_delta, "delta_market_purchase_cost", market_delta) or 0.0),
            )
        ),
    }
    expenses["total"] = float(sum(expenses.values()))

    network_losses_total = float(
        max(0.0, float(getattr(base_delta, "delta_network_losses_cost", 0.0) or 0.0))
    )
    penalties_total = float(max(0.0, float(getattr(base_delta, "delta_penalties", 0.0) or 0.0)))
    risk_total = float(
        max(0.0, float(getattr(base_delta, "delta_risk_penalty", 0.0) or 0.0))
        + max(0.0, float(model_risk_premium or 0.0))
    )
    total_losses_and_risks = float(network_losses_total + penalties_total + risk_total)
    losses_and_risks = {
        "network_losses": network_losses_total,
        "penalties": penalties_total,
        "overload_penalties": float(max(0.0, overload_penalties)),
        "deficit_penalties": float(max(0.0, deficit_penalties)),
        "risk_total": risk_total,
        "flags": list(getattr(base_delta, "flags", [])),
        "total": total_losses_and_risks,
    }

    net_profit = float(income["total"] - expenses["total"] - total_losses_and_risks)
    gross_profit_before_bid = float(_gross_profit_before_bid(net_profit, current_price))
    net_profit_at_recommended_bid = float(
        _profit_after_bid(gross_profit_before_bid, recommended_bid)
    )
    net_profit_at_max_bid = float(_profit_after_bid(gross_profit_before_bid, max_bid))
    roi = float(net_profit / current_price) if current_price > 0 else 0.0
    payback = float(current_price / net_profit) if net_profit > 0 else None
    result = {
        "utility_total": float(base_delta.delta_total),
        "net_profit": net_profit,
        "net_profit_at_current_price": net_profit,
        "gross_profit_before_bid": gross_profit_before_bid,
        "net_profit_at_recommended_bid": net_profit_at_recommended_bid,
        "net_profit_at_max_bid": net_profit_at_max_bid,
        "remaining_budget_after_recommended_bid": float(
            _retained_budget(remaining_budget, recommended_bid)
        ),
        "remaining_budget_after_max_bid": float(_retained_budget(remaining_budget, max_bid)),
        "roi": roi,
        "payback_ratio": payback,
        "threshold_bid": float(hard_bid),
    }
    decomposition = {
        "served_load_revenue": float(max(0.0, served_load_revenue)),
        "avoided_market_purchase_value": float(max(0.0, avoided_market_purchase_value)),
        "export_revenue": float(max(0.0, export_revenue)),
        "fuel_and_taxes": expenses["fuel_and_taxes"],
        "storage_operating_cost": expenses["storage_operating_cost"],
        "network_loss_cost": losses_and_risks["network_losses"],
        "overload_penalties": losses_and_risks["overload_penalties"],
        "deficit_penalties": losses_and_risks["deficit_penalties"],
    }
    ui_rows: List[Dict[str, Any]] = []

    def push_row(
        *,
        key: str,
        label: str,
        value: float,
        group: str,
        always: bool = False,
        emphasis: bool = False,
    ) -> None:
        numeric = float(value)
        if not always and abs(numeric) <= 1e-6:
            return
        ui_rows.append(
            {
                "key": key,
                "label": label,
                "value": numeric,
                "group": group,
                "emphasis": bool(emphasis),
            }
        )

    push_row(
        key="served_load_revenue",
        label="Доход от объектов",
        value=income["served_load_revenue"],
        group="income",
    )
    push_row(
        key="export_revenue",
        label="Доход от экспорта",
        value=income["export_revenue"],
        group="income",
    )
    push_row(
        key="avoided_market_purchase_value",
        label="Эффект замещения рыночной покупки",
        value=income["avoided_market_purchase_value"],
        group="income",
    )
    push_row(
        key="eco_value",
        label="Экологический вклад",
        value=income["eco_value"],
        group="income",
    )
    push_row(
        key="entry_price",
        label="Цена входа",
        value=expenses["entry_price"],
        group="expense",
        always=True,
    )
    push_row(
        key="contract_costs",
        label="Контрактные расходы",
        value=expenses["contract_costs"],
        group="expense",
    )
    push_row(
        key="fuel_and_taxes",
        label="Топливо и налоги",
        value=expenses["fuel_and_taxes"],
        group="expense",
    )
    push_row(
        key="storage_operating_cost",
        label="Эксплуатация накопителя",
        value=expenses["storage_operating_cost"],
        group="expense",
    )
    push_row(
        key="market_purchase",
        label="Допзакупка на рынке",
        value=expenses["market_purchase"],
        group="expense",
    )
    push_row(
        key="network_losses",
        label="Сетевые потери",
        value=losses_and_risks["network_losses"],
        group="loss",
    )
    push_row(
        key="overload_penalties",
        label="Штрафы за перегруз",
        value=losses_and_risks["overload_penalties"],
        group="loss",
    )
    push_row(
        key="deficit_penalties",
        label="Штрафы за дефицит",
        value=losses_and_risks["deficit_penalties"],
        group="loss",
    )
    push_row(
        key="penalties",
        label="Штрафы всего",
        value=losses_and_risks["penalties"],
        group="loss",
    )
    push_row(
        key="risk_total",
        label="Риск-премия",
        value=losses_and_risks["risk_total"],
        group="loss",
    )
    push_row(
        key="net_profit",
        label="Чистая прибыль при текущей цене",
        value=net_profit,
        group="result",
        always=True,
        emphasis=True,
    )
    push_row(
        key="net_profit_after_recommended_bid",
        label="Чистая прибыль после рекомендуемой ставки",
        value=net_profit_at_recommended_bid,
        group="result",
        always=True,
    )
    push_row(
        key="net_profit_after_max_bid",
        label="Чистая прибыль после максимальной ставки",
        value=net_profit_at_max_bid,
        group="result",
        always=True,
    )
    push_row(
        key="remaining_budget_after_recommended_bid",
        label="Остаток бюджета после рекомендуемой ставки",
        value=_retained_budget(remaining_budget, recommended_bid),
        group="result",
        always=True,
    )
    push_row(
        key="remaining_budget_after_max_bid",
        label="Остаток бюджета после максимальной ставки",
        value=_retained_budget(remaining_budget, max_bid),
        group="result",
        always=True,
    )
    return {
        "income": income,
        "expenses": expenses,
        "losses_and_risks": losses_and_risks,
        "result": result,
        "decomposition": decomposition,
        "ui_rows": ui_rows,
    }


def resolve_working_bid(
    *,
    decision_summary: Dict[str, Any],
    financial_breakdown: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    del financial_breakdown
    summary = dict(decision_summary or {})
    cautious_bid = max(
        0.0,
        _as_float(
            summary.get("recommended_bid_safe", summary.get("cautious_bid", 0.0)),
            0.0,
        ),
    )
    target_bid = max(
        0.0,
        _as_float(
            summary.get("recommended_bid_balanced", summary.get("target_bid", 0.0)),
            0.0,
        ),
    )
    hard_ceiling_bid = max(0.0, _as_float(summary.get("hard_ceiling_bid"), 0.0))
    budget_adjusted_bid = max(
        0.0,
        _as_float(summary.get("budget_adjusted_bid", target_bid), 0.0),
    )
    remaining_budget = max(0.0, _as_float(summary.get("budget_remaining"), 0.0))
    conservative_value = max(
        0.0,
        _as_float((summary.get("conservative_utility") or {}).get("value"), 0.0),
    )
    p_win = _as_float(summary.get("p_win"), 0.0)
    serious_competitors = int(_as_float(summary.get("serious_competitors"), 0.0))

    if remaining_budget <= 0.0:
        return {
            "working_bid": 0.0,
            "working_bid_source": "zero",
            "working_bid_reason": "Balanced bid равна 0: бюджет сессии исчерпан.",
        }

    if conservative_value <= 0.0:
        return {
            "working_bid": 0.0,
            "working_bid_source": "zero",
            "working_bid_reason": (
                "Balanced bid равна 0: консервативная полезность неположительная."
            ),
        }

    if budget_adjusted_bid > 0.0:
        if budget_adjusted_bid + 1e-9 < target_bid:
            reason = (
                "Balanced bid ограничена бюджетом: "
                f"{BUDGET_PRESERVATION_NOTE}"
            )
            source = "budget_adjusted"
        else:
            preserved_budget = max(0.0, remaining_budget - budget_adjusted_bid)
            reason = (
                "Balanced bid рассчитана по маржинальной портфельной модели "
                f"(p_win={p_win:.2f}, k={serious_competitors}). "
                f"После сделки сохранится {preserved_budget:.2f}. {BUDGET_PRESERVATION_NOTE}"
            )
            source = "balanced"
        return {
            "working_bid": float(budget_adjusted_bid),
            "working_bid_source": source,
            "working_bid_reason": reason,
        }

    if hard_ceiling_bid <= 0.0:
        reason = (
            "Balanced bid равна 0: даже hard ceiling не оставляет положительной "
            "прибыли после покупки."
        )
    elif cautious_bid > 0.0 and remaining_budget + 1e-9 < cautious_bid:
        reason = (
            "Balanced bid равна 0: доступный остаток ниже safe bid, "
            "поэтому лот лучше пропустить."
        )
    else:
        reason = (
            "Balanced bid равна 0: лот не формирует оправданную цену входа в текущем "
            "контексте."
        )
    return {
        "working_bid": 0.0,
        "working_bid_source": "zero",
        "working_bid_reason": reason,
    }


def _valuation_model_v3(
    *,
    p_worst: float,
    p_base: float,
    p_best: float,
    p_exp: float,
    entry_price_total: float,
    horizon_ticks: int,
    remaining_budget: float,
    evaluation_cfg: Dict[str, Any],
    role_profile: Dict[str, Any] | None = None,
    portfolio_synergy: float = 0.0,
    system_fit_score: float = 0.0,
    p_win: float | None = None,
    serious_competitors: int | None = None,
    conservative_utility_meta: Dict[str, Any] | None = None,
    rules_cfg: Dict[str, Any] | None = None,
    scarcity_signal: float = 0.0,
    available_lots_count: int = 0,
) -> Dict[str, Any]:
    merged_cfg = dict(rules_cfg or {})
    merged_cfg["evaluation"] = dict(evaluation_cfg or {})
    auction_cfg = _auction_bid_cfg(merged_cfg)
    horizon = max(1, int(horizon_ticks or 1))
    role_payload = dict(role_profile or {})
    if conservative_utility_meta is None:
        conservative_utility_meta = conservative_utility(
            weighted_expected=float(p_exp),
            net_profit_worst=float(p_worst),
            net_profit_base=float(p_base),
            net_profit_best=float(p_best),
            cfg=merged_cfg,
        )
    conservative_value = float(conservative_utility_meta.get("value", 0.0) or 0.0)
    scenario_volatility = float(conservative_utility_meta.get("sigma_profit", 0.0) or 0.0)
    downside_gap = max(0.0, float(p_base) - float(p_worst))
    risk_lambda = float(evaluation_cfg.get("risk_lambda", 0.25))
    volatility_lambda = float(evaluation_cfg.get("volatility_lambda", 0.15))
    risk_premium = float(risk_lambda * downside_gap + volatility_lambda * scenario_volatility)
    risk_ratio = float(risk_premium / max(abs(float(p_exp)), abs(float(p_base)), 1.0))
    risk_band = _risk_band(p_worst=float(p_worst), risk_ratio=float(risk_ratio))
    reserve_required = _reserve_requirements(
        remaining_budget=float(remaining_budget),
        p_exp=float(p_exp),
        evaluation_cfg=evaluation_cfg,
    )
    reserve_impact = _reserve_impact(
        reserve_required=float(reserve_required),
        remaining_budget=float(remaining_budget),
        entry_price_total=float(entry_price_total),
        evaluation_cfg=evaluation_cfg,
    )
    fit_multiplier = _fit_multiplier(
        system_fit_score=float(system_fit_score),
        evaluation_cfg=evaluation_cfg,
    )
    scarcity_phase_multiplier = _scarcity_phase_multiplier(
        p_win=float(
            p_win
            if p_win is not None
            else auction_cfg.get("pwin_default", DEFAULT_AUCTION_BID_CFG["pwin_default"])
        ),
        scarcity_signal=float(scarcity_signal),
        available_lots_count=int(available_lots_count),
        rules_cfg=merged_cfg,
        evaluation_cfg=evaluation_cfg,
    )
    marginal_portfolio_value = max(0.0, float(p_exp))
    fit_adjusted_value = float(marginal_portfolio_value * fit_multiplier)
    scarcity_adjusted_value = float(fit_adjusted_value * scarcity_phase_multiplier)
    opportunity_cost = _opportunity_cost(
        entry_price_total=float(entry_price_total),
        remaining_budget=float(remaining_budget),
        marginal_value=float(scarcity_adjusted_value),
        evaluation_cfg=evaluation_cfg,
    )
    target_raw = float(
        scarcity_adjusted_value - float(risk_premium) - float(reserve_impact) - float(opportunity_cost)
    )
    gross_expected_profit_before_bid = float(max(0.0, float(p_exp) + max(0.0, float(entry_price_total))))
    p_win_value = (
        float(p_win)
        if p_win is not None
        else float(auction_cfg.get("pwin_default", DEFAULT_AUCTION_BID_CFG["pwin_default"]))
    )
    competitors_value = int(
        serious_competitors
        if serious_competitors is not None
        else int(round(auction_cfg.get("serious_competitors_default", 3.0)))
    )
    positive_profit_floor = float(
        max(
            0.0,
            auction_cfg.get(
                "min_positive_profit_floor",
                DEFAULT_AUCTION_BID_CFG["min_positive_profit_floor"],
            ),
        )
    )
    safe_factor = float(evaluation_cfg.get("safe_factor", auction_cfg.get("safe_multiplier", 0.82)))
    aggressive_factor = float(
        evaluation_cfg.get("aggressive_factor", auction_cfg.get("aggressive_multiplier", 1.22))
    )
    extra_risk_guard = float(
        max(0.0, risk_premium) * float(evaluation_cfg.get("safe_risk_guard_share", 0.35))
        + max(0.0, scenario_volatility) * float(evaluation_cfg.get("safe_volatility_guard_lambda", 0.10))
    )
    economic_cap = max(
        0.0,
        min(
            gross_expected_profit_before_bid - positive_profit_floor,
            scarcity_adjusted_value - max(0.0, risk_premium),
            conservative_value,
        ),
    )
    cash_available = max(0.0, float(remaining_budget))
    hard_cap = _clamp(min(economic_cap, cash_available), 0.0, cash_available)
    target_cap = min(hard_cap, max(0.0, cash_available - reserve_required))
    target_bid = _clamp(target_raw, 0.0, target_cap)
    safe_bid = _clamp(target_raw * safe_factor - extra_risk_guard, 0.0, target_cap)
    aggressive_bid = _clamp(max(target_bid, target_raw * aggressive_factor), 0.0, hard_cap)
    bid_share = (
        float(target_bid / max(1.0, scarcity_adjusted_value))
        if scarcity_adjusted_value > 0
        else 0.0
    )
    risk_adjusted_net_profit = float(
        float(p_exp)
        - float(risk_premium)
        - float(reserve_impact)
        - float(opportunity_cost)
        + 0.20 * float(portfolio_synergy)
    )
    reason_codes: List[str] = []
    if fit_multiplier < 0.99:
        reason_codes.append("low_system_fit")
    if fit_multiplier > 1.01:
        reason_codes.append("strong_system_fit")
    if reserve_impact > 0.0:
        reason_codes.append("reserve_protection")
    if opportunity_cost > 0.0:
        reason_codes.append("liquidity_opportunity_cost")
    if risk_premium > 0.0:
        reason_codes.append("risk_premium")
    if scarcity_phase_multiplier > 1.02:
        reason_codes.append("scarcity_bonus")
    elif scarcity_phase_multiplier < 0.98:
        reason_codes.append("scarcity_discount")
    if target_cap + 1e-9 < hard_cap:
        reason_codes.append("target_cap_by_reserve")
    if hard_cap + 1e-9 < economic_cap:
        reason_codes.append("hard_cap_by_cash")
    if target_bid <= 0.0:
        reason_codes.append("no_positive_target_bid")
    explainability = {
        "portfolio_delta_value": float(marginal_portfolio_value),
        "system_fit_adjustment": {
            "score": float(system_fit_score),
            "multiplier": float(fit_multiplier),
            "adjusted_value": float(fit_adjusted_value),
        },
        "risk_premium": float(risk_premium),
        "reserve_required": float(reserve_required),
        "reserve_impact": float(reserve_impact),
        "opportunity_cost": float(opportunity_cost),
        "scarcity_phase_multiplier": float(scarcity_phase_multiplier),
        "final_caps": {
            "cash_available": float(cash_available),
            "economic_cap": float(max(0.0, economic_cap)),
            "target_cap": float(max(0.0, target_cap)),
            "hard_cap": float(max(0.0, hard_cap)),
        },
        "reason_codes": reason_codes,
    }
    recommended_bid = float(target_bid)
    return {
        "model": "valuation_model_v3",
        "model_name": "auction_bid_model",
        "model_version": str(
            auction_cfg.get("auction_bid_model_version", DEFAULT_AUCTION_BID_CFG["auction_bid_model_version"])
        ),
        "profile": str(role_payload.get("dominant_role") or "mixed"),
        "risk_band": risk_band,
        "horizon_ticks": int(horizon),
        "p_worst": float(p_worst),
        "p_base": float(p_base),
        "p_best": float(p_best),
        "p_exp": float(p_exp),
        "scenario_volatility": float(scenario_volatility),
        "downside_gap": float(downside_gap),
        "risk_ratio": float(risk_ratio),
        "risk_premium": float(risk_premium),
        "reserve_margin": float(reserve_required),
        "reserve_required": float(reserve_required),
        "reserve_impact": float(reserve_impact),
        "opportunity_cost": float(opportunity_cost),
        "conservative_utility": dict(conservative_utility_meta),
        "portfolio_synergy": float(portfolio_synergy),
        "portfolio_delta_value": float(marginal_portfolio_value),
        "system_fit_score": float(system_fit_score),
        "system_fit_multiplier": float(fit_multiplier),
        "scarcity_signal": float(scarcity_signal),
        "scarcity_phase_multiplier": float(scarcity_phase_multiplier),
        "p_win": float(p_win_value),
        "serious_competitors": int(competitors_value),
        "gross_expected_profit_before_bid": float(gross_expected_profit_before_bid),
        "cap_share": float(bid_share),
        "bid_share": float(bid_share),
        "target_raw": float(target_raw),
        "safe_bid": float(safe_bid),
        "target_bid": float(target_bid),
        "hard_cap": float(hard_cap),
        "cautious_bid": float(safe_bid),
        "hard_ceiling_bid": float(hard_cap),
        "budget_adjusted_bid": float(target_bid),
        "working_bid": float(recommended_bid),
        "recommended_bid": float(recommended_bid),
        "recommended_bid_safe": float(safe_bid),
        "recommended_bid_balanced": float(target_bid),
        "recommended_bid_aggressive": float(aggressive_bid),
        "max_bid": float(hard_cap),
        "net_profit_at_safe_bid": float(_profit_after_bid(gross_expected_profit_before_bid, safe_bid)),
        "net_profit_at_balanced_bid": float(
            _profit_after_bid(gross_expected_profit_before_bid, target_bid)
        ),
        "net_profit_at_aggressive_bid": float(
            _profit_after_bid(gross_expected_profit_before_bid, aggressive_bid)
        ),
        "net_profit_at_recommended_bid": float(
            _profit_after_bid(gross_expected_profit_before_bid, recommended_bid)
        ),
        "net_profit_at_max_bid": float(_profit_after_bid(gross_expected_profit_before_bid, hard_cap)),
        "remaining_budget_after_safe_bid": float(_retained_budget(cash_available, safe_bid)),
        "remaining_budget_after_balanced_bid": float(_retained_budget(cash_available, target_bid)),
        "remaining_budget_after_aggressive_bid": float(
            _retained_budget(cash_available, aggressive_bid)
        ),
        "remaining_budget_after_recommended_bid": float(
            _retained_budget(cash_available, recommended_bid)
        ),
        "remaining_budget_after_max_bid": float(_retained_budget(cash_available, hard_cap)),
        "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
        "explainability": explainability,
    }


def _latest_eval_state(session: GameSession, lot: Lot) -> Dict[str, Any]:
    latest = (
        db.session.query(EvaluationResult)
        .filter_by(session_id=session.id, lot_id=lot.id)
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .first()
    )
    if latest is None:
        return {"is_stale": False, "stale_reason": ""}
    return {"is_stale": bool(latest.is_stale), "stale_reason": latest.stale_reason or ""}


def _simulate_portfolio_with_lots(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    forecast: Optional[Forecast],
    cfg: Dict[str, Any],
    portfolio_lots: Sequence[Lot] | None = None,
) -> Tuple[
    Dict[str, ScenarioSnapshot], List[int], Dict[str, Dict[int, float]], Dict[str, Dict[int, float]]
]:
    if forecast is not None:
        factors, profiles, ticks = _period_series(list(forecast.periods))
    else:
        factors, profiles, ticks = _bundled_series()
    simulated_lots = [*list(portfolio_lots or []), *list(lots)]
    assets = _collect_assets(session, simulated_lots)
    snapshots = {
        "base": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="base"
        ),
        "worst": _simulate_scenario(
            assets=assets,
            factors=factors,
            profiles=profiles,
            ticks=ticks,
            cfg=cfg,
            scenario="worst",
        ),
        "best": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="best"
        ),
    }
    return snapshots, ticks, factors, profiles


def _simulate_portfolio_with_prepared_series(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    cfg: Dict[str, Any],
    ticks: Sequence[int],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    portfolio_lots: Sequence[Lot] | None = None,
) -> Dict[str, ScenarioSnapshot]:
    simulated_lots = [*list(portfolio_lots or []), *list(lots)]
    assets = _collect_assets(session, simulated_lots)
    return {
        "base": _simulate_scenario(
            assets=assets,
            factors=factors,
            profiles=profiles,
            ticks=list(ticks),
            cfg=cfg,
            scenario="base",
        ),
        "worst": _simulate_scenario(
            assets=assets,
            factors=factors,
            profiles=profiles,
            ticks=list(ticks),
            cfg=cfg,
            scenario="worst",
        ),
        "best": _simulate_scenario(
            assets=assets,
            factors=factors,
            profiles=profiles,
            ticks=list(ticks),
            cfg=cfg,
            scenario="best",
        ),
    }


def _build_delta_pack(
    base_state: Dict[str, ScenarioSnapshot], with_state: Dict[str, ScenarioSnapshot]
) -> Dict[str, DeltaSnapshot]:
    return {
        "base": _to_delta(base_state["base"], with_state["base"]),
        "worst": _to_delta(base_state["worst"], with_state["worst"]),
        "best": _to_delta(base_state["best"], with_state["best"]),
    }


def prepare_fast_scoring_context(
    *,
    session: GameSession,
    forecast: Optional[Forecast] = None,
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
    available_lots: Sequence[Lot] | None = None,
) -> Dict[str, Any]:
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast.id if forecast is not None else None
    )
    selected_forecast = analysis_ctx["forecast"]
    forecast_summary = dict(analysis_ctx["forecast_summary"])
    compatibility = dict(forecast_summary.get("compatibility_report") or {})
    if not bool(forecast_summary.get("is_compatible", True)):
        raise ForecastCompatibilityError(compatibility)
    rules_cfg = dict(session.ruleset.config_json or {})
    portfolio_lots_list = list(portfolio_lots or [])
    portfolio_reserved_spend = _reserved_lot_spend(portfolio_lots_list)
    total_reserved_spend = float(max(0.0, reserved_spend) + portfolio_reserved_spend)
    base_state, ticks, factors, profiles = _simulate_portfolio_with_lots(
        session=session,
        lots=[],
        forecast=selected_forecast,
        cfg=rules_cfg,
        portfolio_lots=portfolio_lots_list,
    )
    reference_available_lots = list(available_lots or [])
    if not reference_available_lots:
        reference_available_lots = [
            lot for lot in _session_lots(session) if str(lot.status or "") == "available"
        ]
    return {
        "analysis_ctx": analysis_ctx,
        "forecast": selected_forecast,
        "forecast_summary": forecast_summary,
        "compatibility": compatibility,
        "rules_cfg": rules_cfg,
        "base_state": base_state,
        "ticks": list(ticks),
        "factors": factors,
        "profiles": profiles,
        "portfolio_lots": portfolio_lots_list,
        "total_reserved_spend": float(total_reserved_spend),
        "reference_available_lots": reference_available_lots,
    }


def evaluate_lot_bundle(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
    available_lots: Sequence[Lot] | None = None,
    fast_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    del strategy
    selected_strategy = "unified"
    if fast_context is None:
        fast_context = prepare_fast_scoring_context(
            session=session,
            forecast=forecast,
            portfolio_lots=portfolio_lots,
            reserved_spend=reserved_spend,
            available_lots=available_lots,
        )
    analysis_ctx = dict(fast_context.get("analysis_ctx") or {})
    forecast = cast(Optional[Forecast], fast_context.get("forecast"))
    forecast_summary = dict(fast_context.get("forecast_summary") or {})
    compatibility = dict(fast_context.get("compatibility") or {})
    if not bool(forecast_summary.get("is_compatible", True)):
        raise ForecastCompatibilityError(compatibility)
    rules_cfg = dict(fast_context.get("rules_cfg") or {})
    auction_cfg = _auction_bid_cfg(rules_cfg)
    portfolio_lots = list(fast_context.get("portfolio_lots") or portfolio_lots or [])
    candidate_lots = list(lots)
    reference_available_lots = list(available_lots or fast_context.get("reference_available_lots") or [])
    if not reference_available_lots:
        reference_available_lots = [
            lot for lot in _session_lots(session) if str(lot.status or "") == "available"
        ]
    if not reference_available_lots:
        reference_available_lots = list(candidate_lots)
    total_reserved_spend = float(
        fast_context.get("total_reserved_spend")
        if fast_context.get("total_reserved_spend") is not None
        else max(0.0, reserved_spend) + _reserved_lot_spend(portfolio_lots)
    )
    base_state = cast(Dict[str, ScenarioSnapshot], fast_context.get("base_state") or {})
    ticks = list(cast(Sequence[int], fast_context.get("ticks") or []))
    factors = cast(Dict[str, Dict[int, float]], fast_context.get("factors") or {})
    profiles = cast(Dict[str, Dict[int, float]], fast_context.get("profiles") or {})
    if not base_state or not ticks:
        base_state, ticks, factors, profiles = _simulate_portfolio_with_lots(
            session=session,
            lots=[],
            forecast=forecast,
            cfg=rules_cfg,
            portfolio_lots=portfolio_lots,
        )
    with_state = _simulate_portfolio_with_prepared_series(
        session=session,
        lots=candidate_lots,
        cfg=rules_cfg,
        ticks=ticks,
        factors=factors,
        profiles=profiles,
        portfolio_lots=portfolio_lots,
    )
    deltas = _build_delta_pack(base_state, with_state)
    d_base = deltas["base"]
    d_worst = deltas["worst"]
    d_best = deltas["best"]

    remaining_budget = _remaining_budget(session, reserved_spend=total_reserved_spend)
    entry_price_total = float(sum(_lot_reference_price(lot) for lot in candidate_lots))

    lot_assets = _collect_lot_assets(candidate_lots)
    standalone_state = _simulate_assets(
        assets=lot_assets,
        factors=factors,
        profiles=profiles,
        ticks=ticks,
        cfg=rules_cfg,
    )
    role_profile = _lot_role_profile(candidate_lots)
    system_check = _lot_connection_outlook(session=session, lots=candidate_lots)

    scenario_breakdown = {
        "worst": _scenario_row(
            label="Worst",
            scenario="worst",
            delta_obj=d_worst,
            current_price=entry_price_total,
            pwin=0.0,
            remaining_budget=remaining_budget,
        ),
        "base": _scenario_row(
            label="Base",
            scenario="base",
            delta_obj=d_base,
            current_price=entry_price_total,
            pwin=0.0,
            remaining_budget=remaining_budget,
        ),
        "best": _scenario_row(
            label="Best",
            scenario="best",
            delta_obj=d_best,
            current_price=entry_price_total,
            pwin=0.0,
            remaining_budget=remaining_budget,
        ),
    }

    net_profit_worst = float(scenario_breakdown["worst"]["net_profit"])
    net_profit_base = float(scenario_breakdown["base"]["net_profit"])
    net_profit_best = float(scenario_breakdown["best"]["net_profit"])
    expected_net_profit = _weighted_expected(
        rules_cfg,
        base=net_profit_base,
        worst=net_profit_worst,
        best=net_profit_best,
    )
    standalone_net_profit_base = float(standalone_state["base"].utility_score - entry_price_total)
    standalone_net_profit_worst = float(standalone_state["worst"].utility_score - entry_price_total)
    standalone_net_profit_best = float(standalone_state["best"].utility_score - entry_price_total)
    standalone_expected_net_profit = _weighted_expected(
        rules_cfg,
        base=standalone_net_profit_base,
        worst=standalone_net_profit_worst,
        best=standalone_net_profit_best,
    )
    portfolio_synergy = float(expected_net_profit - standalone_expected_net_profit)
    conservative_utility_meta = conservative_utility(
        weighted_expected=float(expected_net_profit),
        net_profit_worst=float(net_profit_worst),
        net_profit_base=float(net_profit_base),
        net_profit_best=float(net_profit_best),
        cfg=rules_cfg,
    )
    pwin_payload = estimate_lot_win_probability(
        lots=candidate_lots,
        session=session,
        available_lots=reference_available_lots,
        conservative_utility_value=float(conservative_utility_meta["value"]),
        portfolio_synergy=float(portfolio_synergy),
    )
    competitors_payload = estimate_serious_competitors(
        lots=candidate_lots,
        session=session,
        pwin_payload=pwin_payload,
    )

    evaluation_cfg = dict(rules_cfg.get("evaluation", {}) or {})
    valuation_model = _valuation_model_v3(
        p_worst=float(net_profit_worst),
        p_base=float(net_profit_base),
        p_best=float(net_profit_best),
        p_exp=float(expected_net_profit),
        entry_price_total=float(entry_price_total),
        horizon_ticks=len(ticks),
        remaining_budget=float(remaining_budget),
        evaluation_cfg=evaluation_cfg,
        role_profile=role_profile,
        portfolio_synergy=portfolio_synergy,
        system_fit_score=float(system_check.get("system_fit_score", 0.0) or 0.0),
        p_win=float(pwin_payload["p_win"]),
        serious_competitors=int(competitors_payload["serious_competitors"]),
        conservative_utility_meta=conservative_utility_meta,
        rules_cfg=rules_cfg,
        scarcity_signal=float(pwin_payload.get("scarcity_signal", 0.0) or 0.0),
        available_lots_count=len(reference_available_lots),
    )

    recommended_bid_safe = float(valuation_model.get("recommended_bid_safe", 0.0) or 0.0)
    recommended_bid_balanced = float(valuation_model.get("recommended_bid_balanced", 0.0) or 0.0)
    recommended_bid_aggressive = float(
        valuation_model.get("recommended_bid_aggressive", 0.0) or 0.0
    )
    hard_ceiling_bid = float(valuation_model.get("hard_ceiling_bid", 0.0) or 0.0)
    max_bid = float(valuation_model.get("max_bid", 0.0) or 0.0)
    risk_premium = float(valuation_model.get("risk_premium", 0.0) or 0.0)
    reserve_margin = float(valuation_model.get("reserve_margin", 0.0) or 0.0)
    risk_adjusted_net_profit = float(valuation_model.get("risk_adjusted_net_profit", 0.0) or 0.0)
    bid_share = float(valuation_model.get("bid_share", 0.0) or 0.0)
    gross_expected_profit_before_bid = float(
        valuation_model.get("gross_expected_profit_before_bid", 0.0) or 0.0
    )
    p_win = float(pwin_payload["p_win"])
    serious_competitors = int(competitors_payload["serious_competitors"])
    topology_blocks_valuation = bool(system_check.get("topology_blocks_valuation"))
    if bool(system_check.get("topology_invalid")) and topology_blocks_valuation:
        recommended_bid_safe = 0.0
        recommended_bid_balanced = 0.0
        recommended_bid_aggressive = 0.0
        hard_ceiling_bid = 0.0
        max_bid = 0.0
        valuation_model["recommended_bid_safe"] = 0.0
        valuation_model["recommended_bid_balanced"] = 0.0
        valuation_model["recommended_bid_aggressive"] = 0.0
        valuation_model["target_bid"] = 0.0
        valuation_model["cautious_bid"] = 0.0
        valuation_model["hard_ceiling_bid"] = 0.0
        valuation_model["max_bid"] = 0.0
        valuation_model["budget_adjusted_bid"] = 0.0
        valuation_model["working_bid"] = 0.0
        valuation_model["net_profit_at_recommended_bid"] = float(gross_expected_profit_before_bid)
        valuation_model["net_profit_at_max_bid"] = float(gross_expected_profit_before_bid)
        valuation_model["remaining_budget_after_recommended_bid"] = float(remaining_budget)
        valuation_model["remaining_budget_after_max_bid"] = float(remaining_budget)
        risk_adjusted_net_profit = min(0.0, float(risk_adjusted_net_profit))
        valuation_model["risk_adjusted_net_profit"] = float(risk_adjusted_net_profit)

    if bool(system_check.get("topology_invalid")) and topology_blocks_valuation:
        scenario_breakdown = {
            key: {
                **dict(row),
                "recommended_bid": 0.0,
                "bid_ceiling": 0.0,
                "net_profit_at_recommended_bid": float(
                    row.get("gross_profit_before_bid", 0.0) or 0.0
                ),
                "net_profit_at_bid_ceiling": float(
                    row.get("gross_profit_before_bid", 0.0) or 0.0
                ),
                "remaining_budget_after_recommended_bid": float(remaining_budget),
                "explanation": str(row.get("explanation") or ""),
                "comment": str(row.get("comment") or ""),
            }
            for key, row in scenario_breakdown.items()
        }
    else:
        scenario_breakdown = {
            key: _populate_scenario_bid_metrics(
                row=dict(row),
                scenario=key,
                p_win=p_win,
                serious_competitors=serious_competitors,
                cfg=rules_cfg,
                remaining_budget=remaining_budget,
            )
            for key, row in scenario_breakdown.items()
        }

    weights = strategy_weights(rules_cfg, selected_strategy)
    delta_profit = (
        float(d_base.delta_income)
        - float(d_base.delta_penalties)
        - float(d_base.delta_contracts)
        - float(d_base.delta_market_net)
        - float(d_base.delta_fuel_and_taxes)
        - float(d_base.delta_storage_operating_cost)
        - float(d_base.delta_network_losses_cost)
    )
    delta_balance = float(-d_base.delta_penalties)
    delta_network_stability = float(-d_base.delta_risk_penalty - d_base.delta_network_losses_cost)
    delta_storage_flex = float(
        d_base.role_breakdown.get("storage", 0.0) + d_base.role_breakdown.get("infrastructure", 0.0)
    )
    delta_green = float(d_base.delta_eco_points)
    delta_risk = float(max(0.0, d_base.delta_total - d_worst.delta_total))
    score = (
        float(weights["w1_economy"]) * delta_profit
        + float(weights["w2_balance"]) * delta_balance
        + float(weights["w3_stability"]) * delta_network_stability
        + float(weights["w4_green"]) * delta_green
        + float(weights["w5_flex"]) * delta_storage_flex
        - float(weights["w6_risk"]) * delta_risk
    )
    if bool(system_check.get("topology_invalid")) and topology_blocks_valuation:
        score = 0.0

    confidence = _clamp(
        1.0 - min(0.6, abs(d_best.delta_total - d_worst.delta_total) / 1200.0), 0.0, 1.0
    )
    reasons = _human_reasons(d_base, top_k=5)
    risk_commentary = (
        "Риск контролируемый: сценарный разброс умеренный."
        if str(valuation_model.get("risk_band") or "high") == "low"
        else "Риск повышен: учитывайте сценарный разброс и консервативную полезность."
    )
    if bool(system_check.get("topology_invalid")) and topology_blocks_valuation:
        risk_commentary = (
            "Оценка ставок временно заблокирована: сначала исправьте топологические ошибки "
            "в энергосистеме."
        )
    elif bool(system_check.get("topology_invalid")):
        risk_commentary = (
            "Топология сессии требует доработки, но оценка лота на аукционе не блокируется; "
            "риски вынесены в post-purchase advisory."
        )
    elif str(system_check.get("status") or "") == "blocked":
        risk_commentary = (
            "Часть объектов даёт слабый connection fit. Для аукциона это отражено снижением "
            "консервативной полезности и ставки."
        )
    strategy_fit_text = (
        "Единый анализ: target bid рассчитывается как маржинальная портфельная оценка."
        if recommended_bid_balanced > 0
        else "Единый анализ: лот не формирует оправданную target bid в текущих условиях."
    )
    network_readiness = network_readiness_summary(session)
    analysis_warnings: List[str] = []
    if bool(network_readiness.get("action_required")):
        network_warning = (
            "После покупки добавленные объекты нужно подключить в энергосистеме. Это advisory "
            "и не блокирует аукционную оценку текущего лота."
        )
        analysis_warnings.append(network_warning)

    budget_adjusted_bid = float(max(0.0, recommended_bid_balanced))
    working_bid = float(recommended_bid_balanced)
    if remaining_budget <= 0.0:
        working_bid_source = "zero"
    elif working_bid + 1e-9 < float(
        valuation_model.get("recommended_bid_balanced", working_bid) or working_bid
    ):
        working_bid_source = "budget_adjusted"
    elif working_bid > 0.0:
        working_bid_source = "balanced"
    else:
        working_bid_source = "zero"

    if bool(system_check.get("topology_invalid")) and topology_blocks_valuation:
        working_bid_reason = (
            "Ставка равна 0: топология энергосистемы некорректна, оценка ставок заблокирована."
        )
    elif remaining_budget <= 0.0:
        working_bid_reason = "Ставка равна 0: бюджет сессии исчерпан."
    elif float(conservative_utility_meta.get("value", 0.0) or 0.0) <= 0.0:
        working_bid_reason = (
            "Ставка равна 0: консервативная полезность неположительная после учёта волатильности."
        )
    elif working_bid <= 0.0:
        working_bid_reason = (
            "Ставка равна 0: маржинальная портфельная модель не формирует оправданный вход."
        )
    else:
        working_bid_reason = (
            "Target bid рассчитан из маржинальной ценности портфеля с учётом fit, "
            "риска, резерва и стоимости ликвидности."
        )

    if max_bid <= 0.0:
        max_bid_reason = "Max justified bid равен 0: нет экономически оправданного потолка."
    elif max_bid + 1e-9 < hard_ceiling_bid:
        max_bid_reason = (
            "Max justified bid ограничен бюджетом сессии. " + BUDGET_PRESERVATION_NOTE
        )
    else:
        max_bid_reason = (
            "Max justified bid ограничен hard ceiling: после покупки должна оставаться "
            "положительная консервативная прибыль."
        )

    recommended_bid_reason = (
        "Target bid — рабочая ставка по умолчанию: компромисс между безопасным входом "
        "и маржинальной ценностью портфеля."
        if working_bid > 0.0
        else working_bid_reason
    )

    financial_breakdown = _financial_breakdown(
        base_delta=d_base,
        current_price=entry_price_total,
        hard_bid=hard_ceiling_bid,
        recommended_bid=working_bid,
        max_bid=max_bid,
        remaining_budget=remaining_budget,
        model_risk_premium=risk_premium,
    )

    decision_summary = {
        "recommended_bid_safe": float(recommended_bid_safe),
        "recommended_bid_balanced": float(recommended_bid_balanced),
        "recommended_bid_aggressive": float(recommended_bid_aggressive),
        "safe_bid": float(recommended_bid_safe),
        "cautious_bid": float(recommended_bid_safe),
        "target_bid": float(recommended_bid_balanced),
        "hard_cap": float(hard_ceiling_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "max_bid": float(max_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "budget_remaining": float(remaining_budget),
        "recommended_bid": float(working_bid),
        "working_bid": float(working_bid),
        "working_bid_source": str(working_bid_source),
        "working_bid_reason": str(working_bid_reason),
        "recommended_bid_reason": str(recommended_bid_reason),
        "max_bid_reason": str(max_bid_reason),
        "expected_net_profit": float(expected_net_profit),
        "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
        "model_working_bid": float(valuation_model.get("working_bid", 0.0) or 0.0),
        "portfolio_synergy": float(portfolio_synergy),
        "system_fit_score": float(system_check.get("system_fit_score", 0.0) or 0.0),
        "system_fit_multiplier": float(valuation_model.get("system_fit_multiplier", 1.0) or 1.0),
        "bid_formula": "portfolio_marginal_allpay_v2",
        "legacy_bid_formula": "pwin_aware_allpay",
        "bid_share": float(bid_share),
        "base_share": float(bid_share),
        "gross_expected_profit_before_bid": float(gross_expected_profit_before_bid),
        "p_win": float(p_win),
        "serious_competitors": int(serious_competitors),
        "conservative_utility": dict(conservative_utility_meta),
        "reserve_required": float(valuation_model.get("reserve_required", 0.0) or 0.0),
        "reserve_impact": float(valuation_model.get("reserve_impact", 0.0) or 0.0),
        "opportunity_cost": float(valuation_model.get("opportunity_cost", 0.0) or 0.0),
        "scarcity_phase_multiplier": float(
            valuation_model.get("scarcity_phase_multiplier", 1.0) or 1.0
        ),
        "auction_bid_model_version": str(
            auction_cfg.get("auction_bid_model_version", DEFAULT_AUCTION_BID_CFG["auction_bid_model_version"])
        ),
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "explainability": dict(valuation_model.get("explainability") or {}),
        "net_profit_at_recommended_bid": float(
            financial_breakdown.get("result", {}).get("net_profit_at_recommended_bid", 0.0) or 0.0
        ),
        "net_profit_at_max_bid": float(
            financial_breakdown.get("result", {}).get("net_profit_at_max_bid", 0.0) or 0.0
        ),
        "remaining_budget_after_recommended_bid": float(
            financial_breakdown.get("result", {}).get("remaining_budget_after_recommended_bid", 0.0)
            or 0.0
        ),
        "remaining_budget_after_max_bid": float(
            financial_breakdown.get("result", {}).get("remaining_budget_after_max_bid", 0.0)
            or 0.0
        ),
        "net_profit_at_safe_bid": float(
            valuation_model.get("net_profit_at_safe_bid", 0.0) or 0.0
        ),
        "net_profit_at_balanced_bid": float(
            valuation_model.get("net_profit_at_balanced_bid", 0.0) or 0.0
        ),
        "net_profit_at_aggressive_bid": float(
            valuation_model.get("net_profit_at_aggressive_bid", 0.0) or 0.0
        ),
        "remaining_budget_after_safe_bid": float(
            valuation_model.get("remaining_budget_after_safe_bid", 0.0) or 0.0
        ),
        "remaining_budget_after_balanced_bid": float(
            valuation_model.get("remaining_budget_after_balanced_bid", 0.0) or 0.0
        ),
        "remaining_budget_after_aggressive_bid": float(
            valuation_model.get("remaining_budget_after_aggressive_bid", 0.0) or 0.0
        ),
    }

    decision_factors = {
        "analysis_mode": "unified",
        "entry_price": float(entry_price_total),
        "remaining_budget_before_bid": float(remaining_budget),
        "max_affordable_bid": float(max_bid),
        "expected_net_profit": float(expected_net_profit),
        "gross_expected_profit_before_bid": float(gross_expected_profit_before_bid),
        "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
        "portfolio_synergy": float(portfolio_synergy),
        "system_fit_score": float(system_check.get("system_fit_score", 0.0) or 0.0),
        "risk_band": str(valuation_model.get("risk_band") or "high"),
        "scenario_volatility": float(valuation_model.get("scenario_volatility", 0.0) or 0.0),
        "downside_gap": float(valuation_model.get("downside_gap", 0.0) or 0.0),
        "risk_premium": float(risk_premium),
        "reserve_margin": float(reserve_margin),
        "reserve_required": float(valuation_model.get("reserve_required", 0.0) or 0.0),
        "reserve_impact": float(valuation_model.get("reserve_impact", 0.0) or 0.0),
        "opportunity_cost": float(valuation_model.get("opportunity_cost", 0.0) or 0.0),
        "system_fit_multiplier": float(valuation_model.get("system_fit_multiplier", 1.0) or 1.0),
        "scarcity_phase_multiplier": float(
            valuation_model.get("scarcity_phase_multiplier", 1.0) or 1.0
        ),
        "bid_share": float(bid_share),
        "safe_bid": float(recommended_bid_safe),
        "target_bid": float(recommended_bid_balanced),
        "hard_cap": float(hard_ceiling_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "recommended_bid": float(working_bid),
        "max_bid": float(max_bid),
        "recommended_bid_safe": float(recommended_bid_safe),
        "recommended_bid_balanced": float(recommended_bid_balanced),
        "recommended_bid_aggressive": float(recommended_bid_aggressive),
        "p_win": float(p_win),
        "serious_competitors": int(serious_competitors),
        "worst_case_profit": float(net_profit_worst),
        "base_case_profit": float(net_profit_base),
        "best_case_profit": float(net_profit_best),
    }

    metrics = {
        "delta_score": float(d_base.delta_total),
        "delta_profit": float(delta_profit),
        "delta_balance": float(delta_balance),
        "delta_green_score": float(delta_green),
        "delta_storage_flexibility": float(delta_storage_flex),
        "delta_network_stability": float(delta_network_stability),
        "delta_risk": float(delta_risk),
        "weighted_expected": float(expected_net_profit),
        "weights": weights,
        "decision_factors": decision_factors,
        "scenario_delta": {
            "base": asdict(d_base),
            "worst": asdict(d_worst),
            "best": asdict(d_best),
        },
        "scenarios": dict(scenario_breakdown),
        "decomposition": dict(financial_breakdown.get("decomposition") or {}),
        "bids": {
            "recommended_bid_safe": float(recommended_bid_safe),
            "recommended_bid_balanced": float(recommended_bid_balanced),
            "recommended_bid_aggressive": float(recommended_bid_aggressive),
            "safe_bid": float(recommended_bid_safe),
            "cautious_bid": float(recommended_bid_safe),
            "target_bid": float(recommended_bid_balanced),
            "hard_cap": float(hard_ceiling_bid),
            "hard_ceiling_bid": float(hard_ceiling_bid),
            "max_bid": float(max_bid),
            "budget_adjusted_bid": float(budget_adjusted_bid),
            "budget_remaining": float(remaining_budget),
            "expected_net_profit": float(expected_net_profit),
            "gross_expected_profit_before_bid": float(gross_expected_profit_before_bid),
            "bid_share": float(bid_share),
            "model_working_bid": float(valuation_model.get("working_bid", 0.0) or 0.0),
            "risk_premium": float(risk_premium),
            "reserve_margin": float(reserve_margin),
            "reserve_required": float(valuation_model.get("reserve_required", 0.0) or 0.0),
            "reserve_impact": float(valuation_model.get("reserve_impact", 0.0) or 0.0),
            "opportunity_cost": float(valuation_model.get("opportunity_cost", 0.0) or 0.0),
            "system_fit_multiplier": float(
                valuation_model.get("system_fit_multiplier", 1.0) or 1.0
            ),
            "scarcity_phase_multiplier": float(
                valuation_model.get("scarcity_phase_multiplier", 1.0) or 1.0
            ),
            "portfolio_synergy": float(portfolio_synergy),
            "system_fit_score": float(system_check.get("system_fit_score", 0.0) or 0.0),
            "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
            "recommended_bid": float(working_bid),
            "working_bid": float(working_bid),
            "working_bid_source": str(working_bid_source),
            "working_bid_reason": str(working_bid_reason),
            "recommended_bid_reason": str(recommended_bid_reason),
            "max_bid_reason": str(max_bid_reason),
            "net_profit_at_recommended_bid": float(
                decision_summary.get("net_profit_at_recommended_bid", 0.0) or 0.0
            ),
            "net_profit_at_max_bid": float(
                decision_summary.get("net_profit_at_max_bid", 0.0) or 0.0
            ),
            "remaining_budget_after_recommended_bid": float(
                decision_summary.get("remaining_budget_after_recommended_bid", 0.0) or 0.0
            ),
            "remaining_budget_after_max_bid": float(
                decision_summary.get("remaining_budget_after_max_bid", 0.0) or 0.0
            ),
            "p_win": float(p_win),
            "serious_competitors": int(serious_competitors),
            "pwin_signals": dict(pwin_payload),
            "conservative_utility": dict(conservative_utility_meta),
            "bid_model": {
                "version": str(
                    auction_cfg.get(
                        "auction_bid_model_version",
                        DEFAULT_AUCTION_BID_CFG["auction_bid_model_version"],
                    )
                ),
                "name": "portfolio-marginal all-pay",
                "formula": (
                    "target_raw = delta_portfolio * fit * scarcity"
                    " - risk_premium - reserve_impact - opportunity_cost"
                ),
            },
            "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
            "valuation_basis": "portfolio_marginal_allpay_v2",
            "valuation_model": valuation_model,
            "explainability": dict(valuation_model.get("explainability") or {}),
        },
        "portfolio_delta": {
            "net_profit_base": float(net_profit_base),
            "net_profit_worst": float(net_profit_worst),
            "net_profit_best": float(net_profit_best),
            "standalone_net_profit_base": float(standalone_net_profit_base),
            "standalone_net_profit_worst": float(standalone_net_profit_worst),
            "standalone_net_profit_best": float(standalone_net_profit_best),
            "standalone_expected_net_profit": float(standalone_expected_net_profit),
            "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
            "utility_base": float(d_base.delta_total),
            "horizon_ticks": int(len(ticks)),
        },
        "forecast_compatibility": compatibility,
        "role_breakdown": dict(d_base.role_breakdown),
        "synergy": {
            "score": float(portfolio_synergy),
            "standalone_expected_net_profit": float(standalone_expected_net_profit),
            "marginal_expected_net_profit": float(expected_net_profit),
        },
        "system_check": dict(system_check),
        "network_readiness": dict(network_readiness),
        "role_profile": dict(role_profile),
    }

    return {
        "summary_score": float(score),
        "scenario_breakdown": scenario_breakdown,
        "financial_breakdown": financial_breakdown,
        "decision_summary": decision_summary,
        "reasons": reasons,
        "explanation": " ".join(reasons) if reasons else "Нет подробного объяснения.",
        "risk_commentary": risk_commentary,
        "strategy_fit_text": strategy_fit_text,
        "analysis_warnings": list(analysis_warnings),
        "confidence": float(confidence),
        "metrics": metrics,
        "system_check": dict(system_check),
        "network_readiness": dict(network_readiness),
        "role_profile": dict(role_profile),
        "forecast_context": dict(analysis_ctx["forecast_context"]),
        "forecast_summary": forecast_summary,
        "analysis_context": {
            "mode": "forecast",
            "analysis_mode": "unified",
            "mode_label": "С прогнозом",
            "source": analysis_ctx["forecast_context"]["source"],
            "source_label": analysis_ctx["forecast_context"]["source_label"],
            "forecast_id": analysis_ctx["forecast_context"]["forecast_id"],
            "forecast_name": analysis_ctx["forecast_context"]["forecast_name"],
        },
        "forecast_compatibility": compatibility,
        "decision_factors": decision_factors,
        "portfolio_context": _portfolio_context(
            session,
            reserved_spend=total_reserved_spend,
            extra_portfolio_lots=portfolio_lots,
        ),
        "recommended_bid_safe": float(recommended_bid_safe),
        "recommended_bid_balanced": float(recommended_bid_balanced),
        "recommended_bid_aggressive": float(recommended_bid_aggressive),
        "recommended_bid_soft": float(recommended_bid_safe),
        "recommended_bid_hard": float(recommended_bid_balanced),
        "safe_bid": float(recommended_bid_safe),
        "cautious_bid": float(recommended_bid_safe),
        "target_bid": float(recommended_bid_balanced),
        "hard_cap": float(hard_ceiling_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "recommended_bid": float(working_bid),
        "max_bid": float(max_bid),
        "p_win": float(p_win),
        "serious_competitors": int(serious_competitors),
        "bid_model": {
            "version": str(
                auction_cfg.get(
                    "auction_bid_model_version",
                    DEFAULT_AUCTION_BID_CFG["auction_bid_model_version"],
                )
            ),
            "formula": (
                "target_raw = delta_portfolio * fit * scarcity"
                " - risk_premium - reserve_impact - opportunity_cost"
            ),
        },
        "bid_explainability": dict(valuation_model.get("explainability") or {}),
        "conservative_utility": dict(conservative_utility_meta),
        "recommended_bid_reason": str(recommended_bid_reason),
        "max_bid_reason": str(max_bid_reason),
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "working_bid": float(working_bid),
        "working_bid_source": str(working_bid_source),
        "working_bid_reason": str(working_bid_reason),
    }


def evaluate_lot(
    *,
    session: GameSession,
    lot: Lot,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    mode: Optional[str] = None,
    persist: bool = True,
    available_lots: Sequence[Lot] | None = None,
    fast_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    del mode
    payload = evaluate_lot_bundle(
        session=session,
        lots=[lot],
        strategy=strategy,
        forecast=forecast,
        available_lots=available_lots,
        fast_context=fast_context,
    )
    stale_state = _latest_eval_state(session, lot)
    payload.update(
        {
            "session_id": session.id,
            "lot_id": lot.id,
            "is_stale": bool(stale_state["is_stale"]),
            "stale_reason": stale_state["stale_reason"],
            "score_definition": (
                "Итоговая полезность: риск-скорректированная прибыль, стабильность, гибкость и совместимость прогноза."
            ),
        }
    )

    if persist:
        row = EvaluationResult(
            session_id=session.id,
            lot_id=lot.id,
            mode="forecast",
            scenario="base",
            summary_score=float(payload["summary_score"]),
            metrics_json={
                **payload["metrics"],
                "analysis_context": payload["analysis_context"],
                "forecast_context": payload["forecast_context"],
                "forecast_summary": payload["forecast_summary"],
                "portfolio_context": payload["portfolio_context"],
                "scenario_breakdown": payload["scenario_breakdown"],
                "financial_breakdown": payload["financial_breakdown"],
                "decision_summary": payload["decision_summary"],
                "reasons": payload["reasons"],
                "risk_commentary": payload["risk_commentary"],
                "strategy_fit_text": payload["strategy_fit_text"],
            },
            explanation=payload["explanation"],
            recommended_bid_soft=float(payload["recommended_bid_soft"]),
            recommended_bid_hard=float(payload["recommended_bid_hard"]),
            confidence=float(payload["confidence"]),
            is_stale=False,
            stale_reason="",
            stale_marked_at=None,
        )
        db.session.add(row)
        db.session.commit()
        payload["evaluation_id"] = row.id
        payload["is_stale"] = False
        payload["stale_reason"] = ""
    return payload


def rank_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    persist: bool = False,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    lot_list = list(lots)
    available_lots = [lot for lot in lot_list if str(lot.status or "") == "available"]
    fast_context = prepare_fast_scoring_context(
        session=session,
        forecast=forecast,
        available_lots=available_lots,
    )
    for lot in lot_list:
        results.append(
            evaluate_lot(
                session=session,
                lot=lot,
                strategy=strategy,
                forecast=forecast,
                persist=persist,
                available_lots=available_lots,
                fast_context=fast_context,
            )
        )
    results.sort(
        key=lambda item: (
            float(
                ((item.get("metrics") or {}).get("portfolio_delta") or {}).get(
                    "risk_adjusted_net_profit",
                    0.0,
                )
            ),
            float(item.get("summary_score", 0.0)),
        ),
        reverse=True,
    )
    return results


def recommend_best_lot(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    available_lots = [lot for lot in lots if str(lot.status or "") == "available"]
    ranked = rank_lots(
        session=session, lots=available_lots, strategy=strategy, forecast=forecast, persist=False
    )
    if not ranked:
        analysis_ctx = resolve_analysis_context(
            session, forecast_id=forecast.id if forecast else None
        )
        return {
            "best": None,
            "alternatives": [],
            "text": "Нет доступных лотов для рекомендации.",
            "forecast_context": analysis_ctx["forecast_context"],
            "portfolio_context": _portfolio_context(session),
        }
    best = ranked[0]
    alternatives = ranked[1:4]
    text = (
        f"Лучший доступный лот по единой оценке: "
        f"полезность {best['summary_score']:.1f}, рекомендуемая ставка {best['working_bid']:.1f}."
    )
    return {
        "best": best,
        "alternatives": alternatives,
        "recommended_bid": best.get("recommended_bid", best["working_bid"]),
        "decision_summary": best["decision_summary"],
        "analysis_mode": "unified",
        "strategy": "unified",
        "text": text,
        "forecast_context": best["forecast_context"],
        "portfolio_context": best["portfolio_context"],
    }


def strategy_fit(
    *,
    session: GameSession,
    lot: Lot,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    out = evaluate_lot(session=session, lot=lot, forecast=forecast, persist=False)
    decision_factors = dict(out.get("decision_factors") or {})
    rows = [
        {
            "strategy": "unified",
            "summary_score": out["summary_score"],
            "recommended_bid_hard": out["recommended_bid_hard"],
            "recommended_bid_soft": out["recommended_bid_soft"],
            "working_bid": out.get("working_bid", 0.0),
            "confidence": out["confidence"],
            "reason": out["explanation"],
            "decision_factors": decision_factors,
            "forecast_context": out["forecast_context"],
            "portfolio_context": out["portfolio_context"],
        }
    ]
    return {
        "lot_id": lot.id,
        "analysis_mode": "unified",
        "rows": rows,
        "best_strategy": "unified",
        "forecast_context": out["forecast_context"],
        "portfolio_context": out["portfolio_context"],
    }


__all__ = [
    "ForecastCompatibilityError",
    "_financial_breakdown",
    "_scenario_row",
    "evaluate_lot",
    "evaluate_lot_bundle",
    "prepare_fast_scoring_context",
    "rank_lots",
    "recommend_best_lot",
    "strategy_fit",
]
