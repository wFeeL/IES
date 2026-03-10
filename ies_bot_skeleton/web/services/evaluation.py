from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from ies_bot_skeleton.offline.lottool import ensure_lottool_path

from ..extensions import db
from ..models import EvaluationResult, Forecast, GameSession, Lot
from .adapter import lot_to_lottool, session_to_state
from .forecast_service import build_forecast_pack, merge_uploaded_forecasts
from .network import validate_session_network
from .ruleset import strategy_weights

ensure_lottool_path()

from lottool.scoring.marginal import marginal_value  # noqa: E402
from lottool.scoring.scenarios import summarize_delta  # noqa: E402

DEFAULT_WEIGHTED = {"base": 0.50, "worst": 0.35, "best": 0.15, "custom": 0.0}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _weighted_expected(config: Dict[str, Any], base: float, worst: float, best: float) -> float:
    weighted = ((config.get("evaluation", {}) or {}).get("weighted_expected", {}) or {}).copy()
    if not weighted:
        weighted = dict(DEFAULT_WEIGHTED)
    wb = float(weighted.get("base", DEFAULT_WEIGHTED["base"]))
    ww = float(weighted.get("worst", DEFAULT_WEIGHTED["worst"]))
    wbest = float(weighted.get("best", DEFAULT_WEIGHTED["best"]))
    return wb * base + ww * worst + wbest * best


def _lot_component_flexibility(lot: Lot) -> float:
    score = 0.0
    for item in lot.items:
        code = (item.object_type.code if item.object_type else "").lower()
        qty = max(1, int(item.quantity or 1))
        if "storage" in code:
            score += 2.5 * qty
        if "mini" in code or "substation" in code:
            score += 1.2 * qty
    return score


def _lot_component_green(lot: Lot) -> float:
    score = 0.0
    for item in lot.items:
        code = (item.object_type.code if item.object_type else "").lower()
        qty = max(1, int(item.quantity or 1))
        if "wind" in code:
            score += 2.0 * qty
        elif "solar" in code or "ses" in code:
            score += 2.4 * qty
        elif "tps" in code or "tes" in code:
            score -= 1.2 * qty
    return score


def _to_dict(delta_obj: Any) -> Dict[str, Any]:
    return asdict(delta_obj)


def evaluate_lot(
    *,
    session: GameSession,
    lot: Lot,
    mode: str,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    rules_cfg = dict(session.ruleset.config_json or {})
    selected_strategy = strategy or session.selected_strategy
    weights = strategy_weights(rules_cfg, selected_strategy)

    state, owned_items = session_to_state(session, rules_cfg)
    lot_model = lot_to_lottool(lot)

    if mode == "no_forecast":
        forecasts = {}
    elif forecast is not None:
        forecasts = build_forecast_pack(forecast)
    else:
        forecasts = merge_uploaded_forecasts(session.forecasts)

    d_base, d_worst, d_best = marginal_value(state, owned_items, lot_model, forecasts, rules_cfg)

    weighted_expected = _weighted_expected(
        rules_cfg,
        base=float(d_base.delta_total),
        worst=float(d_worst.delta_total),
        best=float(d_best.delta_total),
    )
    delta_profit = (
        float(d_base.delta_income)
        - float(d_base.delta_penalties)
        - float(d_base.delta_contracts)
        - float(d_base.delta_market_net)
        - float(d_base.delta_fuel_and_taxes)
        - float(d_base.delta_network_losses_cost)
    )

    delta_balance = float(-d_base.delta_penalties)
    delta_network_stability = float(-d_base.delta_risk_penalty - d_base.delta_network_losses_cost)
    delta_storage_flex = _lot_component_flexibility(lot)
    delta_green = float(d_base.delta_eco_points) + _lot_component_green(lot)
    delta_risk = float(max(0.0, d_base.delta_total - d_worst.delta_total))
    delta_wear = float(max(0.0, d_base.delta_risk_penalty))
    violations = float(sum(1.0 for flag in d_base.flags if flag.startswith("NETPLAN:")))

    score = (
        float(weights["w1_economy"]) * delta_profit
        + float(weights["w2_balance"]) * delta_balance
        + float(weights["w3_stability"]) * delta_network_stability
        + float(weights["w4_green"]) * delta_green
        + float(weights["w5_flex"]) * delta_storage_flex
        - float(weights["w6_risk"]) * delta_risk
        - float(weights["w7_wear"]) * delta_wear
        - float(weights["w8_violations"]) * violations
    )

    auction_cfg = rules_cfg.get("auction", {}) or {}
    pwin = float(auction_cfg.get("pwin_default", state.assumptions.pwin_default))
    risk_lambda = float(((rules_cfg.get("evaluation", {}) or {}).get("risk_lambda", 0.25)))
    allpay_remaining = max(
        0.0, float(session.budget_total or 0.0) - float(session.allpay_spent or 0.0)
    )

    intrinsic_value = max(0.0, float(d_base.delta_total))
    strategic_value = max(0.0, float(weighted_expected))
    violation_penalty = 15.0 * sum(1.0 for _ in d_base.flags)
    risk_discount = max(0.0, float(d_base.delta_total - d_worst.delta_total)) * risk_lambda
    risk_discount += violation_penalty

    hard_bid = _clamp(pwin * strategic_value - risk_discount, 0.0, allpay_remaining)
    soft_bid = 0.8 * hard_bid
    no_bid = hard_bid <= 0.0 or any(flag.startswith("NETPLAN:") for flag in d_base.flags)

    reasons, _ = summarize_delta(d_base, top_k=3)
    textual_reason = "; ".join(reasons) if reasons else "Недостаточно данных для сильного вывода"

    confidence = 1.0
    if mode == "no_forecast":
        confidence -= 0.2
    spread = abs(float(d_best.delta_total - d_base.delta_total)) + abs(
        float(d_base.delta_total - d_worst.delta_total)
    )
    confidence -= min(0.5, spread / 800.0)
    confidence -= min(0.2, 0.03 * len(d_base.flags))

    network_issues = validate_session_network(list(session.objects))
    hard_errors = [x for x in network_issues if x.severity == "error"]
    if hard_errors:
        confidence -= 0.2
    confidence = _clamp(confidence, 0.0, 1.0)

    metrics = {
        "score": float(score),
        "delta_score": float(d_base.delta_total),
        "delta_profit": float(delta_profit),
        "delta_balance": float(delta_balance),
        "delta_green_score": float(delta_green),
        "delta_storage_flexibility": float(delta_storage_flex),
        "delta_network_stability": float(delta_network_stability),
        "delta_risk": float(delta_risk),
        "intrinsic_value": float(intrinsic_value),
        "strategic_value": float(strategic_value),
        "risk_discount": float(risk_discount),
        "recommended_bid_soft": float(soft_bid),
        "recommended_bid_hard": float(hard_bid),
        "no_bid_flag": bool(no_bid),
        "confidence": float(confidence),
        "weights": weights,
        "weighted_expected": float(weighted_expected),
        "flags": list(d_base.flags),
        "network_issues": [x.to_dict() for x in network_issues],
        "scenario_delta": {
            "base": _to_dict(d_base),
            "worst": _to_dict(d_worst),
            "best": _to_dict(d_best),
        },
    }

    payload = {
        "session_id": session.id,
        "lot_id": lot.id,
        "mode": mode,
        "scenario": "base",
        "summary_score": float(score),
        "metrics": metrics,
        "explanation": textual_reason,
        "recommended_bid_soft": float(soft_bid),
        "recommended_bid_hard": float(hard_bid),
        "confidence": float(confidence),
    }

    if persist:
        row = EvaluationResult(
            session_id=session.id,
            lot_id=lot.id,
            mode=mode,
            scenario="base",
            summary_score=float(score),
            metrics_json=metrics,
            explanation=textual_reason,
            recommended_bid_soft=float(soft_bid),
            recommended_bid_hard=float(hard_bid),
            confidence=float(confidence),
        )
        db.session.add(row)
        db.session.commit()
        payload["evaluation_id"] = row.id

    return payload


def compare_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    mode: str,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for lot in lots:
        res = evaluate_lot(
            session=session,
            lot=lot,
            mode=mode,
            strategy=strategy,
            forecast=forecast,
            persist=False,
        )
        results.append(res)
    results.sort(
        key=lambda item: (
            float(item.get("summary_score", 0.0)),
            float((item.get("metrics") or {}).get("delta_score", 0.0)),
        ),
        reverse=True,
    )
    return results


def recommend_best_lot(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    mode: str,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    ranked = compare_lots(
        session=session,
        lots=lots,
        mode=mode,
        strategy=strategy,
        forecast=forecast,
    )
    if not ranked:
        return {"best": None, "alternatives": [], "text": "Нет доступных лотов"}

    best = ranked[0]
    alternatives = ranked[1:4]
    best_metrics = best.get("metrics", {})

    text = (
        f"Лучший лот #{best['lot_id']} для стратегии '{strategy or session.selected_strategy}': "
        f"score={best['summary_score']:.1f}, "
        f"max_bid={best_metrics.get('recommended_bid_hard', 0.0):.1f}."
    )
    return {
        "best": best,
        "alternatives": alternatives,
        "recommended_bid": best_metrics.get("recommended_bid_hard", 0.0),
        "strategy": strategy or session.selected_strategy,
        "text": text,
    }


def strategy_fit(
    *,
    session: GameSession,
    lot: Lot,
    mode: str,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    strategies = [
        "generation",
        "consumer",
        "balanced",
        "storage",
        "eco",
        "risk_averse",
        "aggressive",
    ]
    rows: List[Dict[str, Any]] = []
    for strategy in strategies:
        out = evaluate_lot(
            session=session,
            lot=lot,
            mode=mode,
            strategy=strategy,
            forecast=forecast,
            persist=False,
        )
        rows.append(
            {
                "strategy": strategy,
                "summary_score": out["summary_score"],
                "recommended_bid_hard": out["recommended_bid_hard"],
                "confidence": out["confidence"],
                "reason": out["explanation"],
            }
        )

    rows.sort(key=lambda item: float(item["summary_score"]), reverse=True)
    return {"lot_id": lot.id, "rows": rows, "best_strategy": rows[0]["strategy"] if rows else None}
