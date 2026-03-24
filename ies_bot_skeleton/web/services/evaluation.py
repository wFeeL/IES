from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast

from ...common.budgeting import budget_snapshot
from ...domain.ies2026 import EnergyObject, evaluate_candidate_bundle
from ..extensions import db
from ..models import EvaluationResult, Forecast, GameSession, Lot, LotItem, ObjectInstance
from .analysis_context import resolve_analysis_context
from .forecast_service import build_forecast_pack, session_forecast_compatibility

BUDGET_PRESERVATION_NOTE = "Неиспользованный остаток бюджета сохраняется для следующих аукционов."


class ForecastCompatibilityError(ValueError):
    def __init__(self, report: Dict[str, Any]) -> None:
        reasons = list(report.get("blocking_reasons") or [])
        super().__init__("; ".join(reasons) if reasons else "Прогноз несовместим с объектами сессии.")
        self.report = dict(report)



def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")



def _canonical_code(code: str) -> str:
    normalized = _norm(code)
    aliases = {
        "mini_substation_a": "mini_substation",
        "mini_substation_b": "mini_substation",
        "wind_turbine": "wind",
        "tps": "wind",
        "cyber_solar": "solar",
        "solarrobot": "solar",
        "house": "house_a",
        "housea": "house_a",
        "houseb": "house_b",
    }
    return aliases.get(normalized, normalized)



def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))



def _lot_items(lot: Lot) -> Sequence[LotItem]:
    return cast(Sequence[LotItem], list(lot.items))



def _compatibility_or_error(session: GameSession, forecast: Optional[Forecast]) -> Dict[str, Any]:
    compatibility = session_forecast_compatibility(session=session, forecast=forecast)
    if not compatibility.get("is_compatible", False):
        raise ForecastCompatibilityError(dict(compatibility.get("compatibility_report") or {}))
    return compatibility



def _forecast_pack_for_session(session: GameSession, forecast: Optional[Forecast]) -> Dict[str, Any]:
    if forecast is None:
        compatibility = session_forecast_compatibility(session=session, forecast=None)
        raise ForecastCompatibilityError(dict(compatibility.get("compatibility_report") or {}))
    return build_forecast_pack(forecast)



def _parameters_for_lot_item(item: LotItem) -> Dict[str, Any]:
    params = dict(item.object_type.default_parameters_json or {}) if item.object_type else {}
    params.update(dict(item.overrides_json or {}))
    return params



def _energy_object_from_instance(row: ObjectInstance) -> EnergyObject:
    object_type = row.object_type
    params = dict(row.merged_parameters() or {})
    return EnergyObject(
        object_id=f"obj-{int(row.id)}",
        object_type_id=int(row.object_type_id),
        code=_canonical_code(object_type.code if object_type else ""),
        name=row.custom_name or (object_type.name if object_type else f"Объект {row.id}"),
        category=str(object_type.category if object_type else "consumer"),
        district=str(row.district or params.get("district") or "default"),
        parameters=params,
        source_lot_id=int(row.source_lot_id) if row.source_lot_id else None,
        is_candidate=False,
        is_active=bool(row.is_active),
        parent_id=f"obj-{int(row.parent_instance_id)}" if row.parent_instance_id else None,
        terminals=[],
    )



def _expanded_candidate_objects_from_lot(lot: Lot) -> List[EnergyObject]:
    objects: List[EnergyObject] = []
    for item_index, item in enumerate(_lot_items(lot), start=1):
        params = _parameters_for_lot_item(item)
        quantity = max(1, int(item.quantity or 1))
        base_name = item.object_type.name if item.object_type else f"Тип {item.object_type_id}"
        for copy_index in range(1, quantity + 1):
            object_id = f"lot-{int(lot.id)}-item-{item_index}-{copy_index}"
            suffix = f" #{copy_index}" if quantity > 1 else ""
            object_type = item.object_type
            objects.append(
                EnergyObject(
                    object_id=object_id,
                    object_type_id=int(item.object_type_id),
                    code=_canonical_code(object_type.code if object_type else ""),
                    name=f"{base_name}{suffix}",
                    category=str(object_type.category if object_type else "consumer"),
                    district=str(params.get("district") or "default"),
                    parameters=deepcopy(params),
                    source_lot_id=int(lot.id),
                    is_candidate=True,
                    is_active=True,
                    parent_id=None,
                    terminals=[],
                )
            )
    return objects



def _base_objects(session: GameSession) -> List[EnergyObject]:
    return [_energy_object_from_instance(row) for row in session.objects if row.is_active]



def _role_breakdown(objects: Sequence[EnergyObject]) -> Dict[str, float]:
    counts = {"consumer": 0.0, "generator": 0.0, "storage": 0.0, "infrastructure": 0.0}
    for obj in objects:
        counts[str(obj.category)] = counts.get(str(obj.category), 0.0) + 1.0
    return counts



def _system_check(evaluation) -> Dict[str, Any]:
    critical = [issue.message for issue in evaluation.topology.issues if issue.severity == "critical"]
    warnings = [issue.message for issue in evaluation.topology.issues if issue.severity == "warning"]
    hints = [issue.message for issue in evaluation.topology.issues if issue.severity == "hint"]
    status = "supported"
    if critical:
        status = "blocked"
    elif str(evaluation.topology_risk) != "low":
        status = "risky"
    elif warnings:
        status = "warning"
    return {
        "status": status,
        "message": critical[0] if critical else warnings[0] if warnings else str(evaluation.explanation),
        "critical_blocking_errors": critical,
        "warnings": warnings,
        "optimization_hints": hints,
        "topology_candidates": list(getattr(evaluation.topology, "topology_candidates", []) or []),
        "system_fit_score": round(max(0.0, 1.0 - (0.25 * len(critical) + 0.08 * len(warnings))), 4),
        "recommended_points": sorted({t.connection_point for rows in evaluation.topology.recommended_connections.values() for t in rows if getattr(t, "connection_point", None)}),
        "connection_block_reasons_count": len(critical),
    }



def _risk_total(evaluation) -> float:
    totals = evaluation.base_case.totals
    return float(totals.loss_cost + totals.balancing_penalty + totals.unmet_load_penalty)



def _risk_adjusted_profit(evaluation) -> float:
    spread = abs(float(evaluation.best_case.delta_profit) - float(evaluation.worst_case.delta_profit))
    uncertainty = float(getattr(evaluation, "wind_uncertainty_penalty", 0.0) or 0.0)
    expected = float(getattr(evaluation, "expected_profit_after_purchase", 0.0) or evaluation.expected_delta_profit or 0.0)
    return round(expected - spread * 0.18 - uncertainty, 4)



def _scenario_payload(label: str, report) -> Dict[str, Any]:
    totals = report.totals
    return {
        "label": label.title(),
        "delta_profit": round(float(report.delta_profit), 4),
        "revenue_total": round(float(totals.consumer_revenue + totals.market_revenue), 4),
        "cost_total": round(float(totals.service_cost + totals.market_purchase_cost + totals.loss_cost + totals.balancing_penalty + totals.unmet_load_penalty), 4),
        "net_profit": round(float(report.delta_profit), 4),
        "explanation": "; ".join(report.notes) if report.notes else "",
    }



def _price_role(evaluation) -> str:
    explicit = str(getattr(evaluation, "price_role", "") or "").strip()
    if explicit:
        return explicit
    return "consumer_floor" if evaluation.auction_direction == "descending_consumer_tariff" else "service_ceiling"



def _optimal_purchase_price(evaluation) -> float:
    explicit = float(getattr(evaluation, "optimal_purchase_price", 0.0) or 0.0)
    if explicit > 0.0:
        return explicit
    if evaluation.auction_direction == "descending_consumer_tariff":
        return float(getattr(evaluation, "recommended_walkdown_tariff", 0.0) or evaluation.recommended_bid_or_tariff or 0.0)
    return float(getattr(evaluation, "recommended_bid_ceiling", 0.0) or evaluation.recommended_bid_or_tariff or 0.0)



def _hard_limit(evaluation) -> float:
    explicit = float(getattr(evaluation, "hard_limit", 0.0) or 0.0)
    if explicit > 0.0:
        return explicit
    if evaluation.auction_direction == "descending_consumer_tariff":
        return float(getattr(evaluation, "hard_floor", 0.0) or evaluation.break_even_tariff or 0.0)
    return float(getattr(evaluation, "hard_ceiling", 0.0) or evaluation.break_even_tariff or 0.0)



def _payload_from_evaluation(
    *,
    session: GameSession,
    lot_name: str,
    evaluation,
    forecast_context: Dict[str, Any],
    compatibility: Dict[str, Any],
    candidate_objects: Sequence[EnergyObject],
) -> Dict[str, Any]:
    budget = budget_snapshot(session)
    expected_profit = float(getattr(evaluation, "expected_profit_after_purchase", 0.0) or evaluation.expected_delta_profit or 0.0)
    risk_adjusted_profit = _risk_adjusted_profit(evaluation)
    price_role = _price_role(evaluation)
    optimal_purchase_price = _optimal_purchase_price(evaluation)
    hard_limit = _hard_limit(evaluation)
    system_check = _system_check(evaluation)
    synergy_value = float(getattr(evaluation, "synergy_value", 0.0) or getattr(evaluation, "bundle_synergy_value", 0.0) or 0.0)
    enabler_value = float(getattr(evaluation, "infrastructure_enabler_value", 0.0) or evaluation.enabler_value or 0.0)
    wind_uncertainty_penalty = float(getattr(evaluation, "wind_uncertainty_penalty", 0.0) or 0.0)

    decision_summary = {
        "lot_profile": str(evaluation.lot_profile),
        "analysis_stage": str(getattr(evaluation, "analysis_stage", "pre_auction_lot_valuation")),
        "price_role": price_role,
        "optimal_purchase_price": round(optimal_purchase_price, 4),
        "hard_limit": round(hard_limit, 4),
        "opening_bid": round(float(getattr(evaluation, "recommended_opening_bid", optimal_purchase_price) or optimal_purchase_price), 4),
        "counter_bid": round(float(getattr(evaluation, "recommended_counter_bid", optimal_purchase_price) or optimal_purchase_price), 4),
        "floor_or_ceiling_type": "floor" if price_role == "consumer_floor" else "ceiling",
        "hard_floor_tariff": round(float(getattr(evaluation, "hard_floor", 0.0) or 0.0), 4),
        "hard_ceiling_tariff": round(float(getattr(evaluation, "hard_ceiling", 0.0) or 0.0), 4),
        "expected_profit": round(expected_profit, 4),
        "risk_adjusted_profit": round(risk_adjusted_profit, 4),
        "direct_delta_profit": round(float(evaluation.direct_delta_profit or 0.0), 4),
        "synergy_value": round(synergy_value, 4),
        "enabler_value": round(enabler_value, 4),
        "wind_uncertainty_penalty": round(wind_uncertainty_penalty, 4),
        "recommended_bid_reason": str(evaluation.explanation),
        "budget_remaining": round(float(budget.get("remaining_budget", 0.0) or 0.0), 4),
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
    }

    payload = {
        "summary_score": round(risk_adjusted_profit, 4),
        "forecast_context": dict(forecast_context or {}),
        "portfolio_context": {
            "bought_lots_count": int(sum(1 for row in _session_lots(session) if str(row.status or "") == "bought")),
            "spent_total": float(budget.get("spent_total", 0.0) or 0.0),
            "remaining_budget": float(budget.get("remaining_budget", 0.0) or 0.0),
            "owned_objects_count": len([row for row in session.objects if row.is_active]),
        },
        "scenario_breakdown": {
            "worst": _scenario_payload("worst", evaluation.worst_case),
            "base": _scenario_payload("base", evaluation.base_case),
            "best": _scenario_payload("best", evaluation.best_case),
        },
        "decision_summary": decision_summary,
        "optimal_purchase_price": round(optimal_purchase_price, 4),
        "expected_profit": round(expected_profit, 4),
        "risk_adjusted_profit": round(risk_adjusted_profit, 4),
        "hard_limit": round(hard_limit, 4),
        "price_role": price_role,
        "lot_profile": str(evaluation.lot_profile),
        "direct_delta_profit": round(float(evaluation.direct_delta_profit or 0.0), 4),
        "synergy_value": round(synergy_value, 4),
        "enabler_value": round(enabler_value, 4),
        "wind_uncertainty_penalty": round(wind_uncertainty_penalty, 4),
        "topology_feasibility": str(getattr(evaluation, "topology_feasibility", system_check.get("status", "feasible"))),
        "topology_risk": str(evaluation.topology_risk),
        "market_risk": str(evaluation.market_risk),
        "balancing_risk": str(evaluation.balancing_risk),
        "loss_risk": str(evaluation.loss_risk),
        "working_bid": round(optimal_purchase_price, 4),
        "working_bid_source": "optimal_purchase_price",
        "working_bid_reason": str(evaluation.explanation),
        "recommended_bid": round(optimal_purchase_price, 4),
        "max_bid": round(hard_limit, 4),
        "reasons": [str(evaluation.explanation), *list(getattr(evaluation, "synergy_notes", []) or [])],
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "system_check": system_check,
        "metrics": {
            "forecast_compatibility": compatibility,
            "portfolio_delta": {
                "net_profit_base": round(expected_profit, 4),
                "risk_adjusted_net_profit": round(risk_adjusted_profit, 4),
                "direct_delta_profit": round(float(evaluation.direct_delta_profit or 0.0), 4),
                "enabler_value": round(enabler_value, 4),
            },
            "role_breakdown": _role_breakdown(candidate_objects),
            "synergy": {
                "score": round(synergy_value + enabler_value, 4),
                "standalone_expected_net_profit": round(float(evaluation.direct_delta_profit or 0.0), 4),
                "marginal_expected_net_profit": round(expected_profit, 4),
            },
            "system_check": system_check,
            "storage_value": {
                "arbitrage": round(float(evaluation.storage_value.arbitrage), 4),
                "balancing": round(float(evaluation.storage_value.balancing), 4),
                "reserve": round(float(evaluation.storage_value.reserve), 4),
                "anti_dumping_support": round(float(getattr(evaluation.storage_value, 'anti_dumping_support', 0.0)), 4),
            },
            "bids": {
                "optimal_purchase_price": round(optimal_purchase_price, 4),
                "hard_limit": round(hard_limit, 4),
                "valuation_model": {
                    "model": "unified_lot_optimizer_2026",
                    "profile": str(evaluation.lot_profile),
                    "price_role": price_role,
                    "optimal_purchase_price": round(optimal_purchase_price, 4),
                    "hard_limit": round(hard_limit, 4),
                    "expected_profit": round(expected_profit, 4),
                    "risk_adjusted_profit": round(risk_adjusted_profit, 4),
                    "wind_uncertainty_penalty": round(wind_uncertainty_penalty, 4),
                    "assumptions": list(getattr(evaluation, "assumptions", []) or []),
                },
            },
        },
        "explanation": str(evaluation.explanation),
        "lot_name": lot_name,
    }
    return payload



def _save_evaluation(*, session: GameSession, lot: Lot, payload: Dict[str, Any]) -> None:
    db.session.query(EvaluationResult).filter_by(session_id=int(session.id), lot_id=int(lot.id), mode="ies_2026").delete(synchronize_session=False)
    db.session.add(
        EvaluationResult(
            session_id=int(session.id),
            lot_id=int(lot.id),
            mode="ies_2026",
            scenario="base",
            summary_score=float(payload.get("summary_score", 0.0) or 0.0),
            metrics_json=payload,
            explanation=str(payload.get("explanation", "")),
            recommended_bid_soft=float(payload.get("optimal_purchase_price", 0.0) or 0.0),
            recommended_bid_hard=float(payload.get("hard_limit", 0.0) or 0.0),
            confidence=0.75,
            is_stale=False,
            stale_reason="",
        )
    )
    db.session.commit()



def prepare_fast_scoring_context(
    *,
    session: GameSession,
    forecast: Optional[Forecast] = None,
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
    available_lots: Sequence[Lot] | None = None,
) -> Dict[str, Any]:
    del portfolio_lots, reserved_spend
    analysis_ctx = resolve_analysis_context(session, forecast_id=forecast.id if forecast else None)
    selected_forecast = forecast if forecast is not None else analysis_ctx.get("forecast")
    compatibility = _compatibility_or_error(session, cast(Optional[Forecast], selected_forecast))
    return {
        "forecast": selected_forecast,
        "forecast_pack": _forecast_pack_for_session(session, cast(Optional[Forecast], selected_forecast)),
        "forecast_context": dict(analysis_ctx.get("forecast_context") or {}),
        "compatibility": compatibility,
        "base_objects": _base_objects(session),
        "rules_cfg": dict(session.ruleset.config_json or {}),
        "available_lots": list(available_lots or []),
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
    fast_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    del strategy, portfolio_lots, reserved_spend
    ordered_lots = sorted(list(lots), key=lambda row: int(row.id))
    if not ordered_lots:
        raise ValueError("Для bundle evaluation нужен хотя бы один лот")
    context = fast_context or prepare_fast_scoring_context(session=session, forecast=forecast, available_lots=available_lots)
    base_objects = list(context.get("base_objects") or _base_objects(session))
    forecast_pack = dict(context.get("forecast_pack") or {})
    forecast_context = dict(context.get("forecast_context") or {})
    compatibility = dict(context.get("compatibility") or _compatibility_or_error(session, forecast))
    candidate_objects: List[EnergyObject] = []
    for lot in ordered_lots:
        candidate_objects.extend(_expanded_candidate_objects_from_lot(lot))
    follow_ups: List[Tuple[int, str, Sequence[EnergyObject]]] = []
    if len(ordered_lots) == 1:
        current_id = int(ordered_lots[0].id)
        for row in list(available_lots or context.get("available_lots") or []):
            if int(row.id) == current_id or str(row.status or "") != "available":
                continue
            follow_ups.append((int(row.id), row.name, _expanded_candidate_objects_from_lot(row)))
    evaluation = evaluate_candidate_bundle(
        lot_id=int(ordered_lots[0].id) if len(ordered_lots) == 1 else -1,
        lot_name=" + ".join(lot.name for lot in ordered_lots),
        base_objects=base_objects,
        candidate_objects=candidate_objects,
        forecast_pack=forecast_pack,
        ruleset_config=dict(session.ruleset.config_json or {}),
        follow_up_candidates=follow_ups,
    )
    return _payload_from_evaluation(
        session=session,
        lot_name=" + ".join(lot.name for lot in ordered_lots),
        evaluation=evaluation,
        forecast_context=forecast_context,
        compatibility=compatibility,
        candidate_objects=candidate_objects,
    )



def evaluate_lot(
    *,
    session: GameSession,
    lot: Lot,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    payload = evaluate_lot_bundle(
        session=session,
        lots=[lot],
        strategy=strategy,
        forecast=forecast,
        available_lots=[row for row in _session_lots(session) if str(row.status or "") == "available"],
    )
    if persist:
        _save_evaluation(session=session, lot=lot, payload=payload)
    return payload



def rank_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    persist: bool = False,
) -> List[Dict[str, Any]]:
    del strategy
    rows = list(lots)
    if not rows:
        return []
    fast_context = prepare_fast_scoring_context(session=session, forecast=forecast, available_lots=[row for row in _session_lots(session) if str(row.status or "") == "available"])
    out: List[Dict[str, Any]] = []
    for lot in rows:
        payload = evaluate_lot_bundle(
            session=session,
            lots=[lot],
            forecast=forecast,
            available_lots=cast(Sequence[Lot], fast_context.get("available_lots") or []),
            fast_context=fast_context,
        )
        payload["lot_id"] = int(lot.id)
        out.append(payload)
        if persist:
            _save_evaluation(session=session, lot=lot, payload=payload)
    out.sort(key=lambda row: (float(row.get("risk_adjusted_profit", 0.0) or 0.0), float(row.get("expected_profit", 0.0) or 0.0), -float(row.get("wind_uncertainty_penalty", 0.0) or 0.0)), reverse=True)
    return out



def recommend_best_lot(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    ranked = rank_lots(session=session, lots=list(lots), strategy=strategy, forecast=forecast, persist=False)
    best = ranked[0] if ranked else None
    return {"model": "unified_lot_optimizer_2026", "best_lot": best, "ranked_lots": ranked, "summary": {"count": len(ranked), "best_lot_id": int(best.get("lot_id", 0) or 0) if best else None, "best_expected_profit": float(best.get("expected_profit", 0.0) or 0.0) if best else 0.0}}



def strategy_fit(
    *,
    session: GameSession,
    lot: Lot,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    evaluation = evaluate_lot(session=session, lot=lot, strategy=None, forecast=forecast, persist=False)
    return {
        "model": "unified_lot_optimizer_2026",
        "lot_id": int(lot.id),
        "lot_name": lot.name,
        "topology_risk": str(evaluation.get("topology_risk") or "low"),
        "market_risk": str(evaluation.get("market_risk") or "low"),
        "balancing_risk": str(evaluation.get("balancing_risk") or "low"),
        "loss_risk": str(evaluation.get("loss_risk") or "low"),
        "expected_profit": float(evaluation.get("expected_profit", 0.0) or 0.0),
        "risk_adjusted_profit": float(evaluation.get("risk_adjusted_profit", 0.0) or 0.0),
        "optimal_purchase_price": float(evaluation.get("optimal_purchase_price", 0.0) or 0.0),
        "hard_limit": float(evaluation.get("hard_limit", 0.0) or 0.0),
        "system_check": dict(evaluation.get("system_check") or {}),
        "synergy": dict(((evaluation.get("metrics") or {}).get("synergy") or {})),
        "mounting_requirements": list((evaluation.get("system_check") or {}).get("critical_blocking_errors") or []),
        "conflicts": list((evaluation.get("system_check") or {}).get("warnings") or []),
        "assumptions": list((((evaluation.get("metrics") or {}).get("bids") or {}).get("valuation_model") or {}).get("assumptions") or []),
        "explanation": str(evaluation.get("explanation") or ""),
    }


__all__ = [
    "BUDGET_PRESERVATION_NOTE",
    "ForecastCompatibilityError",
    "evaluate_lot",
    "evaluate_lot_bundle",
    "prepare_fast_scoring_context",
    "rank_lots",
    "recommend_best_lot",
    "strategy_fit",
]
