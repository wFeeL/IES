from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from ies_bot_skeleton.domain.lot_analysis.auction.ev import recommended_bid_range
from ies_bot_skeleton.domain.lot_analysis.scoring.marginal import marginal_value
from ies_bot_skeleton.domain.lot_analysis.scoring.scenarios import summarize_delta

from ..extensions import db
from ..models import EvaluationResult, Forecast, GameSession, Lot
from .adapter import lot_to_domain_lot, session_to_state
from .analysis_context import resolve_analysis_context
from .forecast_service import build_forecast_pack, load_bundled_forecast_pack
from .network import validate_session_network
from .ruleset import strategy_weights
from .ui_text import strategy_label

DEFAULT_WEIGHTED = {"base": 0.50, "worst": 0.35, "best": 0.15, "custom": 0.0}
_REASON_LABELS = {
    "income": "сильный доход по профилю потребления",
    "penalties": "низкие штрафы и дисбаланс",
    "contracts": "приемлемые контрактные затраты",
    "fuel+tax": "контролируемые топливные и налоговые расходы",
    "market": "рынок даёт положительный вклад",
    "net-loss": "сетевые потери остаются под контролем",
    "risk": "риск-премия невысокая",
    "eco": "экологический эффект повышает ценность лота",
}


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


def _spent_total(session: GameSession) -> float:
    return float(
        sum(float(lot.purchase_price or 0.0) for lot in session.lots if str(lot.status or "") == "bought")
    )


def _remaining_budget(session: GameSession) -> float:
    return max(0.0, float(session.budget_total or 0.0) - _spent_total(session))


def _portfolio_context(session: GameSession) -> Dict[str, Any]:
    spent_total = _spent_total(session)
    return {
        "bought_lots_count": sum(1 for lot in session.lots if str(lot.status or "") == "bought"),
        "spent_total": spent_total,
        "remaining_budget": max(0.0, float(session.budget_total or 0.0) - spent_total),
        "owned_objects_count": sum(1 for obj in session.objects if obj.is_active),
    }


def _risk_commentary(*, delta_risk: float, flags: List[str], confidence: float) -> str:
    if any(flag.startswith("NETPLAN:") for flag in flags):
        return "Повышенный риск: у лота есть сетевые ограничения, перед покупкой нужен пересчёт после проверки схемы."
    if delta_risk >= 60:
        return "Риск выше среднего: результат чувствителен к изменению прогноза и внешних условий."
    if delta_risk >= 20:
        return "Умеренный риск: ставку лучше держать в пределах рабочего максимума."
    if confidence < 0.45:
        return "Модель даёт низкую уверенность: данные прогноза или структуры лота требуют дополнительной проверки."
    return "Риск контролируемый: критических ограничений по текущему прогнозу не найдено."


def _strategy_fit_text(
    *,
    strategy: str,
    score: float,
    hard_bid: float,
    delta_total: float,
    weighted_expected: float,
) -> str:
    label = strategy_label(strategy)
    if hard_bid <= 0:
        if delta_total <= 0:
            return f"{label}: лот не улучшает портфель и не поддерживает безопасную ставку."
        return f"{label}: эффект есть, но он недостаточен для рабочей покупки по текущей цене."
    if score >= 80:
        return f"{label}: лот хорошо поддерживает текущую стратегию и бюджетный контекст."
    if score >= 25 or weighted_expected >= 50:
        return f"{label}: соответствие рабочее, покупка допустима при контроле цены."
    return f"{label}: соответствие ограниченное, нужна ручная проверка перед входом в сделку."


def _human_reasons(delta_obj: Any, *, top_k: int = 5) -> List[str]:
    reasons, flags = summarize_delta(delta_obj, top_k=top_k)
    out: List[str] = []
    for item in reasons:
        raw_key, _, raw_value = item.partition(":")
        key = raw_key.strip()
        value = raw_value.strip()
        label = _REASON_LABELS.get(key, key)
        out.append(f"{label}: {value}")
    for flag in list(flags)[:2]:
        out.append(f"Технический флаг: {flag}")
    return out[:top_k]


def _scenario_comment(*, utility_total: float, net_profit: float, recommended_bid: float) -> str:
    if recommended_bid <= 0.0:
        return "Сценарий не поддерживает безопасную ставку по этому лоту."
    if utility_total >= 0 and net_profit >= 0:
        return "Сценарий выглядит рабочим: полезность и чистая прибыль остаются положительными."
    if utility_total >= 0:
        return "Полезность положительная, но чистая прибыль чувствительна к цене входа."
    return "Сценарий слабый: покупка ухудшает общий результат портфеля."


def _scenario_row(
    *,
    label: str,
    delta_obj: Any,
    current_price: float,
    pwin: float,
    remaining_budget: float,
) -> Dict[str, Any]:
    income_total = max(0.0, float(delta_obj.delta_income)) + max(0.0, float(delta_obj.delta_market_net)) + max(0.0, float(delta_obj.delta_eco_value))
    expenses_total = float(current_price) + max(0.0, float(delta_obj.delta_contracts)) + max(0.0, float(delta_obj.delta_fuel_and_taxes)) + max(0.0, -float(delta_obj.delta_market_net))
    losses_total = max(0.0, float(delta_obj.delta_penalties)) + max(0.0, float(delta_obj.delta_network_losses_cost)) + max(0.0, float(delta_obj.delta_risk_penalty))
    utility_total = float(delta_obj.delta_total)
    _, scenario_hard_bid = recommended_bid_range(max(0.0, utility_total), pwin, safety=0.80)
    recommended_bid = _clamp(float(scenario_hard_bid), 0.0, remaining_budget)
    net_profit = income_total - expenses_total - losses_total
    return {
        "label": label,
        "income_total": float(income_total),
        "expenses_total": float(expenses_total),
        "losses_total": float(losses_total),
        "utility_total": float(utility_total),
        "recommended_bid": float(recommended_bid),
        "expected_net_profit_at_current_price": float(net_profit),
        "comment": _scenario_comment(
            utility_total=float(utility_total),
            net_profit=float(net_profit),
            recommended_bid=float(recommended_bid),
        ),
    }


def _financial_breakdown(*, base_delta: Any, current_price: float, hard_bid: float) -> Dict[str, Any]:
    income = {
        "object_income": float(max(0.0, base_delta.delta_income)),
        "market_income": float(max(0.0, base_delta.delta_market_net)),
        "eco_value": float(max(0.0, base_delta.delta_eco_value)),
    }
    income["total"] = float(sum(income.values()))

    expenses = {
        "entry_price": float(current_price),
        "contract_costs": float(max(0.0, base_delta.delta_contracts)),
        "fuel_and_taxes": float(max(0.0, base_delta.delta_fuel_and_taxes)),
        "market_purchase": float(max(0.0, -base_delta.delta_market_net)),
    }
    expenses["total"] = float(sum(expenses.values()))

    losses_and_risks = {
        "network_losses": float(max(0.0, base_delta.delta_network_losses_cost)),
        "penalties": float(max(0.0, base_delta.delta_penalties)),
        "risk_total": float(max(0.0, base_delta.delta_risk_penalty)),
        "flags": list(base_delta.flags),
    }
    losses_and_risks["total"] = float(
        losses_and_risks["network_losses"] + losses_and_risks["penalties"] + losses_and_risks["risk_total"]
    )

    net_profit = float(income["total"] - expenses["total"] - losses_and_risks["total"])
    roi = float(net_profit / current_price) if current_price > 0 else 0.0
    payback = float(current_price / net_profit) if net_profit > 0 else None
    result = {
        "utility_total": float(base_delta.delta_total),
        "net_profit": net_profit,
        "roi": roi,
        "payback_ratio": payback,
        "threshold_bid": float(hard_bid),
    }
    return {
        "income": income,
        "expenses": expenses,
        "losses_and_risks": losses_and_risks,
        "result": result,
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


def evaluate_lot(
    *,
    session: GameSession,
    lot: Lot,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    mode: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    rules_cfg = dict(session.ruleset.config_json or {})
    selected_strategy = strategy or session.selected_strategy
    weights = strategy_weights(rules_cfg, selected_strategy)
    analysis_ctx = resolve_analysis_context(session, forecast_id=forecast.id if forecast is not None else None)
    forecast = analysis_ctx["forecast"]

    state, owned_items = session_to_state(session, rules_cfg)
    lot_model = lot_to_domain_lot(lot)

    if forecast is not None:
        forecasts = build_forecast_pack(forecast)
    else:
        forecasts = load_bundled_forecast_pack()

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
    remaining_budget = _remaining_budget(session)

    intrinsic_value = max(0.0, float(d_base.delta_total))
    strategic_value = max(0.0, float(weighted_expected))
    violation_penalty = 15.0 * sum(1.0 for _ in d_base.flags)
    risk_discount = max(0.0, float(d_base.delta_total - d_worst.delta_total)) * risk_lambda
    risk_discount += violation_penalty

    risk_adjusted_value = max(0.0, float(weighted_expected) - float(risk_discount))
    soft_bid_raw, hard_bid_raw = recommended_bid_range(risk_adjusted_value, pwin, safety=0.80)
    hard_bid = _clamp(hard_bid_raw, 0.0, remaining_budget)
    soft_bid = _clamp(soft_bid_raw, 0.0, hard_bid)

    spread = abs(float(d_best.delta_total - d_base.delta_total)) + abs(float(d_base.delta_total - d_worst.delta_total))
    confidence = 1.0 - min(0.5, spread / 800.0) - min(0.2, 0.03 * len(d_base.flags))

    network_issues = validate_session_network(list(session.objects))
    if any(issue.severity == "error" for issue in network_issues):
        confidence -= 0.2
    confidence = _clamp(confidence, 0.0, 1.0)

    risk_commentary = _risk_commentary(
        delta_risk=float(delta_risk),
        flags=list(d_base.flags),
        confidence=float(confidence),
    )
    strategy_fit_text = _strategy_fit_text(
        strategy=selected_strategy,
        score=float(score),
        hard_bid=float(hard_bid),
        delta_total=float(d_base.delta_total),
        weighted_expected=float(weighted_expected),
    )
    current_price = float(lot.purchase_price if lot.status == "bought" and lot.purchase_price is not None else lot.current_bid or 0.0)
    scenario_breakdown = {
        "worst": _scenario_row(
            label="Worst",
            delta_obj=d_worst,
            current_price=current_price,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
        "base": _scenario_row(
            label="Base",
            delta_obj=d_base,
            current_price=current_price,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
        "best": _scenario_row(
            label="Best",
            delta_obj=d_best,
            current_price=current_price,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
    }
    stop_bid = _clamp(pwin * risk_adjusted_value, 0.0, remaining_budget)
    decision_summary = {
        "soft_bid": float(soft_bid),
        "hard_bid": float(hard_bid),
        "stop_bid": float(stop_bid),
    }
    financial_breakdown = _financial_breakdown(
        base_delta=d_base,
        current_price=current_price,
        hard_bid=hard_bid,
    )
    forecast_context = dict(analysis_ctx["forecast_context"])
    analysis_context = {
        "mode": "forecast",
        "mode_label": "С прогнозом",
        "source": forecast_context["source"],
        "source_label": forecast_context["source_label"],
        "forecast_id": forecast_context["forecast_id"],
        "forecast_name": forecast_context["forecast_name"],
    }
    portfolio_context = _portfolio_context(session)
    stale_state = _latest_eval_state(session, lot)
    reasons = _human_reasons(d_base, top_k=5)

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
        "risk_adjusted_value": float(risk_adjusted_value),
        "risk_discount": float(risk_discount),
        "recommended_bid_soft": float(soft_bid),
        "recommended_bid_hard": float(hard_bid),
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
        "summary_score": float(score),
        "metrics": metrics,
        "recommended_bid_soft": float(soft_bid),
        "recommended_bid_hard": float(hard_bid),
        "confidence": float(confidence),
        "risk_commentary": risk_commentary,
        "strategy_fit_text": strategy_fit_text,
        "analysis_context": analysis_context,
        "forecast_context": forecast_context,
        "forecast_summary": analysis_ctx["forecast_summary"],
        "portfolio_context": portfolio_context,
        "scenario_breakdown": scenario_breakdown,
        "financial_breakdown": financial_breakdown,
        "decision_summary": decision_summary,
        "reasons": reasons,
        "explanation": " ".join(reasons) if reasons else "Нет подробного объяснения.",
        "score_definition": (
            "Итоговая полезность: экономика + баланс + устойчивость + экологичность + гибкость "
            "минус риск, износ и нарушения."
        ),
        "is_stale": bool(stale_state["is_stale"]),
        "stale_reason": stale_state["stale_reason"],
    }

    if persist:
        row = EvaluationResult(
            session_id=session.id,
            lot_id=lot.id,
            mode="forecast",
            scenario="base",
            summary_score=float(score),
            metrics_json={
                **metrics,
                "analysis_context": analysis_context,
                "forecast_context": forecast_context,
                "forecast_summary": analysis_ctx["forecast_summary"],
                "portfolio_context": portfolio_context,
                "scenario_breakdown": scenario_breakdown,
                "financial_breakdown": financial_breakdown,
                "decision_summary": decision_summary,
                "reasons": reasons,
                "risk_commentary": risk_commentary,
                "strategy_fit_text": strategy_fit_text,
            },
            explanation=payload["explanation"],
            recommended_bid_soft=float(soft_bid),
            recommended_bid_hard=float(hard_bid),
            confidence=float(confidence),
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
    for lot in lots:
        results.append(
            evaluate_lot(
                session=session,
                lot=lot,
                strategy=strategy,
                forecast=forecast,
                persist=persist,
            )
        )
    results.sort(
        key=lambda item: (
            float(item.get("summary_score", 0.0)),
            float((item.get("financial_breakdown") or {}).get("result", {}).get("net_profit", 0.0)),
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
        session=session,
        lots=available_lots,
        strategy=strategy,
        forecast=forecast,
        persist=False,
    )
    if not ranked:
        return {
            "best": None,
            "alternatives": [],
            "text": "Нет доступных лотов для рекомендации.",
            "forecast_context": resolve_analysis_context(session, forecast_id=forecast.id if forecast else None)["forecast_context"],
            "portfolio_context": _portfolio_context(session),
        }

    best = ranked[0]
    alternatives = ranked[1:4]
    strategy_name = strategy_label(strategy or session.selected_strategy)
    text = (
        f"Лучший доступный лот для стратегии «{strategy_name}»: итоговая полезность {best['summary_score']:.1f}, "
        f"рабочий максимум ставки {best['decision_summary']['hard_bid']:.1f}."
    )
    return {
        "best": best,
        "alternatives": alternatives,
        "recommended_bid": best["decision_summary"]["hard_bid"],
        "decision_summary": best["decision_summary"],
        "strategy": strategy or session.selected_strategy,
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
                "forecast_context": out["forecast_context"],
                "portfolio_context": out["portfolio_context"],
            }
        )

    rows.sort(key=lambda item: float(item["summary_score"]), reverse=True)
    return {
        "lot_id": lot.id,
        "rows": rows,
        "best_strategy": rows[0]["strategy"] if rows else None,
        "forecast_context": rows[0]["forecast_context"] if rows else resolve_analysis_context(session)["forecast_context"],
        "portfolio_context": rows[0]["portfolio_context"] if rows else _portfolio_context(session),
    }
