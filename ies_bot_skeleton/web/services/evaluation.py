from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, cast

from ...common.budgeting import budget_snapshot
from ...domain.ies2026 import EnergyObject, evaluate_candidate_bundle
from ..extensions import db
from ..models import EvaluationResult, Forecast, GameSession, Lot, LotItem, ObjectInstance
from .analysis_context import resolve_analysis_context
from .forecast_service import build_forecast_pack, load_bundled_forecast_pack, session_forecast_compatibility
from .strategy_catalog import normalize_strategy_code

BUDGET_PRESERVATION_NOTE = (
    "Неиспользованный остаток бюджета сохраняется для следующих аукционов."
)


class ForecastCompatibilityError(ValueError):
    def __init__(self, report: Dict[str, Any]) -> None:
        reasons = list(report.get("blocking_reasons") or [])
        super().__init__("; ".join(reasons) if reasons else "Прогноз несовместим с объектами сессии.")
        self.report = dict(report)


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _plain_dict(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return cast(Dict[str, Any], asdict(value))
    if isinstance(value, dict):
        return dict(value)
    return {}


def _canonical_code(code: str) -> str:
    normalized = _norm(code)
    mapping = {
        "mini_substation_a": "mini_substation",
        "mini_substation_b": "mini_substation",
        "wind_turbine": "wind",
        "wind": "wind",
        "tps": "wind",
        "solar": "solar",
        "cyber_solar": "solar",
        "solarrobot": "solar",
        "house": "house_a",
        "housea": "house_a",
        "house_a": "house_a",
        "houseb": "house_b",
        "house_b": "house_b",
        "main_substation": "main_substation",
        "storage": "storage",
        "hospital": "hospital",
        "factory": "factory",
        "office": "office",
    }
    return mapping.get(normalized, normalized)


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _lot_items(lot: Lot) -> Sequence[LotItem]:
    return cast(Sequence[LotItem], list(lot.items))


def _compatibility_or_error(session: GameSession, forecast: Optional[Forecast]) -> Dict[str, Any]:
    compatibility = session_forecast_compatibility(session=session, forecast=forecast)
    if not compatibility.get("is_compatible", True):
        raise ForecastCompatibilityError(dict(compatibility.get("compatibility_report") or {}))
    return compatibility


def _forecast_pack_for_session(session: GameSession, forecast: Optional[Forecast]) -> Dict[str, Any]:
    if forecast is not None:
        return build_forecast_pack(forecast)
    return load_bundled_forecast_pack()


def _parameters_for_lot_item(item: LotItem) -> Dict[str, Any]:
    params = dict(item.object_type.default_parameters_json or {}) if item.object_type else {}
    params.update(dict(item.overrides_json or {}))
    return params


def _default_connection_point(rules_cfg: Dict[str, Any] | None) -> str:
    network_cfg = dict((rules_cfg or {}).get("network") or {})
    point = str(network_cfg.get("default_connection_point") or "A").strip().upper()
    return point or "A"


def _connection_inputs_from_params(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    inputs = []
    raw_inputs = list(params.get("connection_inputs") or [])
    for idx, row in enumerate(raw_inputs, start=1):
        if not isinstance(row, dict):
            continue
        inputs.append(
            {
                "key": str(row.get("key") or f"in{idx}"),
                "label": str(row.get("label") or f"Ввод {idx}"),
                "required": bool(row.get("required", True)),
                "parent_instance_id": row.get("parent_instance_id"),
                "connection_point": str(
                    row.get("connection_point") or row.get("point") or row.get("slot") or "A"
                ).upper(),
                "load_share": float(row.get("load_share", 1.0) or 1.0),
            }
        )
    return inputs


def _energy_object_from_instance(
    row: ObjectInstance,
    *,
    default_connection_point: str = "A",
) -> EnergyObject:
    object_type = row.object_type
    code = _canonical_code(object_type.code if object_type else "")
    params = dict(row.merged_parameters() or {})
    current_params = dict(row.current_parameters_json or {})
    if "connection_point" not in current_params:
        params["connection_point"] = str(default_connection_point or "A").upper()
    terminals = _connection_inputs_from_params(params)
    return EnergyObject(
        object_id=f"obj-{int(row.id)}",
        object_type_id=int(row.object_type_id),
        code=code,
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


def _expanded_candidate_objects_from_lot(
    lot: Lot,
    *,
    default_connection_point: str = "A",
) -> List[EnergyObject]:
    objects: List[EnergyObject] = []
    for item_index, item in enumerate(_lot_items(lot), start=1):
        params = _parameters_for_lot_item(item)
        overrides = dict(item.overrides_json or {})
        if "connection_point" not in overrides:
            params["connection_point"] = str(default_connection_point or "A").upper()
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


def _base_objects(
    session: GameSession,
    *,
    default_connection_point: str = "A",
) -> List[EnergyObject]:
    return [
        _energy_object_from_instance(row, default_connection_point=default_connection_point)
        for row in session.objects
        if row.is_active
    ]


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
    elif evaluation.topology_risk == "high":
        status = "risky"
    elif warnings:
        status = "neutral"
    risk_score = {"low": 0.92, "medium": 0.62, "high": 0.28}[evaluation.topology_risk]
    return {
        "status": status,
        "message": critical[0] if critical else warnings[0] if warnings else evaluation.explanation,
        "critical_blocking_errors": critical,
        "warnings": warnings,
        "optimization_hints": hints,
        "topology_candidates": list(evaluation.topology.topology_candidates or []),
        "recommended_connections": {
            object_id: [
                {
                    "key": terminal.key,
                    "label": terminal.label,
                    "parent_id": terminal.parent_id,
                    "connection_point": terminal.connection_point,
                    "required": terminal.required,
                    "load_share": terminal.load_share,
                }
                for terminal in terminals
            ]
            for object_id, terminals in evaluation.topology.recommended_connections.items()
        },
        "system_fit_score": round(risk_score, 4),
        "recommended_points": sorted(
            {
                terminal.connection_point
                for terminals in evaluation.topology.recommended_connections.values()
                for terminal in terminals
                if terminal.connection_point
            }
        ),
        "connection_block_reasons_count": len(critical),
    }


def _risk_total(evaluation) -> float:
    totals = evaluation.base_case.totals
    return float(totals.loss_cost + totals.balancing_penalty + totals.unmet_load_penalty)


def _risk_adjusted_profit(evaluation) -> float:
    explicit = _as_float(getattr(evaluation, "risk_adjusted_profit", 0.0), 0.0)
    if explicit != 0.0:
        return round(explicit, 4)
    spread = abs(float(evaluation.best_case.delta_profit) - float(evaluation.worst_case.delta_profit))
    return round(float(evaluation.expected_delta_profit) - spread * 0.18, 4)


def _strategy_score(
    *,
    strategy: str,
    evaluation,
    candidate_objects: Sequence[EnergyObject],
    system_check: Dict[str, Any],
    risk_adjusted: float,
) -> Dict[str, Any]:
    del strategy, candidate_objects, system_check
    score = float(risk_adjusted)
    why = (
        "Единый оптимизатор ИЭС 2026 ранжирует лоты по risk-adjusted profit и не требует "
        "ручного переключения пользовательских стратегий."
    )
    return {
        "strategy": "unified",
        "score": round(float(score), 4),
        "reason": why,
    }


def _scenario_payload(label: str, report) -> Dict[str, Any]:
    totals = report.totals
    return {
        "label": label.title(),
        "delta_profit": round(float(report.delta_profit), 4),
        "revenue_total": round(float(totals.consumer_revenue + totals.market_revenue), 4),
        "cost_total": round(
            float(
                totals.service_cost
                + totals.market_purchase_cost
                + totals.loss_cost
                + totals.balancing_penalty
                + totals.unmet_load_penalty
            ),
            4,
        ),
        "penalties_total": round(float(totals.unmet_load_penalty + totals.balancing_penalty), 4),
        "losses_total": round(float(totals.loss_cost), 4),
        "net_profit": round(float(report.delta_profit), 4),
        "gross_profit_before_bid": round(float(report.delta_profit), 4),
        "utility_score": round(float(report.delta_profit), 4),
        "recommended_bid": 0.0,
        "bid_ceiling": 0.0,
        "net_profit_at_recommended_bid": round(float(report.delta_profit), 4),
        "net_profit_at_bid_ceiling": round(float(report.delta_profit), 4),
        "remaining_budget_after_recommended_bid": 0.0,
        "explanation": "; ".join(report.notes) if report.notes else "",
        "income_total": round(float(totals.consumer_revenue + totals.market_revenue), 4),
        "expenses_total": round(
            float(
                totals.service_cost
                + totals.market_purchase_cost
                + totals.loss_cost
                + totals.balancing_penalty
                + totals.unmet_load_penalty
            ),
            4,
        ),
        "utility_total": round(float(report.delta_profit), 4),
        "expected_net_profit_at_current_price": round(float(report.delta_profit), 4),
        "comment": "; ".join(report.notes) if report.notes else "",
    }


def _profit_at_recommended(evaluation, horizon: int) -> float:
    del horizon
    recommended = float(
        getattr(evaluation, "optimal_purchase_price", evaluation.recommended_bid_or_tariff) or 0.0
    )
    anchor = float(
        getattr(evaluation, "expected_profit_after_purchase", evaluation.expected_delta_profit) or 0.0
    )
    return round(anchor - recommended, 4)


def _bid_levels(evaluation, horizon: int) -> Dict[str, float]:
    if evaluation.auction_direction == "descending_consumer_tariff":
        safe = float(evaluation.recommended_opening_bid or evaluation.maintenance_tariff_total or 0.0)
        balanced = float(
            evaluation.recommended_walkdown_tariff
            or evaluation.recommended_bid_or_tariff
            or evaluation.break_even_tariff
        )
        aggressive = float(evaluation.aggressive_floor or evaluation.break_even_tariff or 0.0)
    else:
        balanced = float(
            evaluation.recommended_bid_ceiling
            or evaluation.recommended_bid_or_tariff
            or evaluation.break_even_tariff
        )
        safe = float(evaluation.soft_ceiling or max(0.0, balanced * 0.88))
        aggressive = min(
            float(evaluation.hard_ceiling or evaluation.break_even_tariff or 0.0),
            max(float(evaluation.recommended_counter_bid or 0.0), balanced * 1.04),
        )
    return {
        "safe": round(safe, 4),
        "balanced": round(balanced, 4),
        "aggressive": round(aggressive, 4),
        "profit_at_recommended": _profit_at_recommended(evaluation, horizon),
    }


def _payload_from_evaluation(
    *,
    session: GameSession,
    strategy: str,
    lot_name: str,
    evaluation,
    forecast_context: Dict[str, Any],
    compatibility: Dict[str, Any],
    candidate_objects: Sequence[EnergyObject],
) -> Dict[str, Any]:
    budget = budget_snapshot(session)
    horizon = int((session.ruleset.config_json or {}).get("time", {}).get("horizon_ticks", 48) or 48)
    auction_cfg = dict((session.ruleset.config_json or {}).get("auction", {}) or {})
    market_cfg = dict((session.ruleset.config_json or {}).get("market", {}) or {})
    bids = _bid_levels(evaluation, horizon)
    risk_total = _risk_total(evaluation)
    system_check = _system_check(evaluation)
    risk_adjusted = _risk_adjusted_profit(evaluation)
    working_reason = str(evaluation.explanation or "")
    if system_check["status"] == "blocked":
        blocking_notes = [str(item).strip() for item in system_check.get("critical_blocking_errors") or []]
        blocking_hint = "; ".join(item for item in blocking_notes if item)
        if blocking_hint and blocking_hint not in working_reason:
            working_reason = (
                f"{working_reason} Topology block: {blocking_hint}."
                if working_reason
                else f"Topology block: {blocking_hint}."
            )
    allpay_limit = float(auction_cfg.get("allpay_limit", 5000.0) or 5000.0)
    allpay_remaining = max(0.0, allpay_limit - float(budget.get("allpay_spent", 0.0) or 0.0))
    bid_constraints = []
    if evaluation.topology_risk != "low":
        bid_constraints.append("topology risk")
    if evaluation.market_risk != "low":
        bid_constraints.append("market risk")
    if evaluation.balancing_risk != "low":
        bid_constraints.append("balancing risk")
    if evaluation.loss_risk != "low":
        bid_constraints.append("loss risk")
    bid_constraints_summary = ", ".join(bid_constraints) if bid_constraints else "риски контролируемы"
    remaining_after = max(0.0, float(budget.get("remaining_budget", 0.0)) - bids["balanced"])
    role_breakdown = _role_breakdown(candidate_objects)
    optimal_purchase_price = _as_float(
        getattr(evaluation, "optimal_purchase_price", evaluation.recommended_bid_or_tariff),
        _as_float(evaluation.recommended_bid_or_tariff, 0.0),
    )
    expected_profit_after_purchase = _as_float(
        getattr(evaluation, "expected_profit_after_purchase", evaluation.expected_delta_profit),
        _as_float(evaluation.expected_delta_profit, 0.0),
    )
    wind_posterior = _plain_dict(getattr(evaluation, "wind_posterior", {}))
    wind_uncertainty_penalty = _as_float(getattr(evaluation, "wind_uncertainty_penalty", 0.0), 0.0)
    topology_feasibility = str(getattr(evaluation, "topology_feasibility", "") or system_check["status"])
    price_role = str(
        getattr(
            evaluation,
            "price_role",
            "consumer_floor" if evaluation.auction_direction == "descending_consumer_tariff" else "service_ceiling",
        )
    )
    budget_adjusted_bid = min(bids["balanced"], float(budget.get("remaining_budget", 0.0)))
    if optimal_purchase_price <= 0.0:
        working_bid_source = "zero"
    elif budget_adjusted_bid + 1e-9 < optimal_purchase_price:
        working_bid_source = "budget_adjusted"
    else:
        working_bid_source = "target"

    financial_breakdown = {
        "income": {
            "consumer_revenue": round(float(evaluation.base_case.totals.consumer_revenue), 4),
            "fixed_tariff_revenue": round(float(evaluation.base_case.totals.fixed_tariff_revenue), 4),
            "exchange_sale_revenue": round(float(evaluation.base_case.totals.exchange_sale_revenue), 4),
            "guaranteed_sale_revenue": round(float(evaluation.base_case.totals.guaranteed_sale_revenue), 4),
            "market_revenue": round(float(evaluation.base_case.totals.market_revenue), 4),
            "total": round(
                float(
                    evaluation.base_case.totals.consumer_revenue
                    + evaluation.base_case.totals.market_revenue
                ),
                4,
            ),
        },
        "expenses": {
            "service_cost": round(float(evaluation.base_case.totals.service_cost), 4),
            "market_purchase": round(float(evaluation.base_case.totals.market_purchase_cost), 4),
            "gp_purchase": round(float(evaluation.base_case.totals.gp_purchase_cost), 4),
            "total": round(
                float(
                    evaluation.base_case.totals.service_cost
                    + evaluation.base_case.totals.market_purchase_cost
                ),
                4,
            ),
        },
        "losses_and_risks": {
            "network_losses": round(float(evaluation.base_case.totals.loss_cost), 4),
            "balancing_penalty": round(float(evaluation.base_case.totals.balancing_penalty), 4),
            "unmet_load_penalty": round(float(evaluation.base_case.totals.unmet_load_penalty), 4),
            "risk_total": round(risk_total, 4),
            "flags": [issue.code for issue in evaluation.topology.issues],
            "total": round(risk_total, 4),
        },
        "result": {
            "net_profit": round(float(expected_profit_after_purchase), 4),
            "net_profit_at_current_price": round(float(expected_profit_after_purchase), 4),
            "gross_profit_before_bid": round(float(expected_profit_after_purchase), 4),
            "risk_adjusted_profit": round(float(risk_adjusted), 4),
            "net_profit_at_recommended_bid": round(float(bids["profit_at_recommended"]), 4),
            "net_profit_at_max_bid": 0.0,
            "remaining_budget_after_recommended_bid": round(float(remaining_after), 4),
            "remaining_budget_after_max_bid": round(float(max(0.0, float(budget.get("remaining_budget", 0.0)) - evaluation.break_even_tariff)), 4),
            "roi": None,
            "payback_ratio": None,
            "threshold_bid": round(float(evaluation.break_even_tariff), 4),
        },
        "ui_rows": [
            {"key": "fixed_tariff_revenue", "label": "Доход от фиксированных тарифов", "value": round(float(evaluation.base_case.totals.fixed_tariff_revenue), 4), "group": "income", "emphasis": False},
            {"key": "exchange_revenue", "label": "Биржевая выручка", "value": round(float(evaluation.base_case.totals.exchange_sale_revenue), 4), "group": "income", "emphasis": False},
            {"key": "gp_sale_revenue", "label": "Выручка от непроданного остатка", "value": round(float(evaluation.base_case.totals.guaranteed_sale_revenue), 4), "group": "income", "emphasis": False},
            {"key": "service_cost", "label": "Расходы на обслуживание", "value": round(float(evaluation.base_case.totals.service_cost), 4), "group": "expense", "emphasis": False},
            {"key": "gp_purchase", "label": "Покупка энергии у ГП", "value": round(float(evaluation.base_case.totals.gp_purchase_cost), 4), "group": "expense", "emphasis": False},
            {"key": "losses", "label": "Потери сети", "value": round(float(evaluation.base_case.totals.loss_cost), 4), "group": "risk", "emphasis": False},
            {"key": "balancing", "label": "Небаланс", "value": round(float(evaluation.base_case.totals.balancing_penalty), 4), "group": "risk", "emphasis": False},
            {"key": "storage", "label": "Вклад накопителей", "value": round(float(evaluation.storage_value.arbitrage + evaluation.storage_value.balancing + evaluation.storage_value.reserve + evaluation.storage_value.anti_dumping_support), 4), "group": "income", "emphasis": False},
            {"key": "delta_profit", "label": "Expected profit after purchase", "value": round(float(expected_profit_after_purchase), 4), "group": "result", "emphasis": True},
            {"key": "risk_adjusted_profit", "label": "Risk-adjusted profit", "value": round(float(risk_adjusted), 4), "group": "result", "emphasis": True},
            {"key": "wind_uncertainty_penalty", "label": "Wind uncertainty penalty", "value": round(float(wind_uncertainty_penalty), 4), "group": "risk", "emphasis": False},
        ],
    }

    decision_summary = {
        "analysis_stage": str(getattr(evaluation, "analysis_stage", "pre_auction_lot_valuation")),
        "lot_profile": str(evaluation.lot_profile),
        "auction_direction": str(evaluation.auction_direction),
        "floor_or_ceiling_type": (
            "floor" if evaluation.auction_direction == "descending_consumer_tariff" else "ceiling"
        ),
        "price_role": price_role,
        "optimal_purchase_price": round(float(optimal_purchase_price), 4),
        "break_even_tariff": round(float(evaluation.break_even_tariff), 4),
        "recommended_bid_or_tariff": round(float(optimal_purchase_price), 4),
        "minimum_acceptable_tariff": round(float(evaluation.minimum_acceptable_tariff or 0.0), 4),
        "recommended_walkdown_tariff": round(float(evaluation.recommended_walkdown_tariff or 0.0), 4),
        "aggressive_floor": round(float(evaluation.aggressive_floor or 0.0), 4),
        "hard_floor": round(float(evaluation.hard_floor or 0.0), 4),
        "maximum_acceptable_service_tariff": round(float(evaluation.maximum_acceptable_service_tariff or 0.0), 4),
        "recommended_bid_ceiling": round(float(evaluation.recommended_bid_ceiling or 0.0), 4),
        "soft_ceiling": round(float(evaluation.soft_ceiling or 0.0), 4),
        "hard_ceiling": round(float(evaluation.hard_ceiling or 0.0), 4),
        "recommended_opening_bid": round(float(evaluation.recommended_opening_bid or 0.0), 4),
        "recommended_counter_bid": round(float(evaluation.recommended_counter_bid or 0.0), 4),
        "hard_limit": round(float(evaluation.hard_limit or 0.0), 4),
        "allpay_trigger_policy": str(evaluation.allpay_trigger_policy or ""),
        "if_allpay_triggered_max_cash_offer": round(
            min(allpay_remaining, max(0.0, float(evaluation.expected_delta_profit))),
            4,
        ),
        "cumulative_allpay_budget_remaining": round(float(allpay_remaining), 4),
        "recommended_bid_safe": bids["safe"],
        "recommended_bid_balanced": bids["balanced"],
        "recommended_bid_aggressive": bids["aggressive"],
        "safe_bid": bids["safe"],
        "cautious_bid": bids["safe"],
        "target_bid": round(float(optimal_purchase_price), 4),
        "hard_cap": round(float(evaluation.break_even_tariff), 4),
        "hard_ceiling_bid": round(float(evaluation.break_even_tariff), 4),
        "budget_adjusted_bid": budget_adjusted_bid,
        "recommended_bid": round(float(optimal_purchase_price), 4),
        "recommended_bid_hard": round(float(evaluation.break_even_tariff), 4),
        "max_bid": round(float(evaluation.break_even_tariff), 4),
        "bid_formula": "unified_lot_optimizer_v2",
        "legacy_bid_formula": "delta_profit_ies_2026_v1",
        "gross_expected_profit_before_bid": round(float(expected_profit_after_purchase), 4),
        "expected_net_profit": round(float(expected_profit_after_purchase), 4),
        "risk_adjusted_net_profit": round(risk_adjusted, 4),
        "risk_adjusted_profit": round(risk_adjusted, 4),
        "direct_delta_profit": round(float(evaluation.direct_delta_profit), 4),
        "enabler_value": round(float(evaluation.enabler_value), 4),
        "bundle_synergy_value": round(float(evaluation.bundle_synergy_value), 4),
        "model_working_bid": round(float(optimal_purchase_price), 4),
        "portfolio_synergy": round(float(evaluation.enabler_value), 4),
        "system_fit_score": round(float(system_check["system_fit_score"]), 4),
        "value_anchor": round(float(expected_profit_after_purchase), 4),
        "adjusted_value": round(risk_adjusted, 4),
        "fit_factor": round(float(system_check["system_fit_score"]), 4),
        "risk_factor": {"low": 0.92, "medium": 0.74, "high": 0.48}[evaluation.market_risk],
        "volatility_factor": max(0.3, 1.0 - abs(float(evaluation.best_case.delta_profit) - float(evaluation.worst_case.delta_profit)) / max(1.0, abs(float(evaluation.expected_delta_profit)) + 1.0)),
        "competition_factor": 1.0,
        "bid_constraints_summary": bid_constraints_summary,
        "cap_bindings": [evaluation.topology_risk, evaluation.market_risk, evaluation.balancing_risk, evaluation.loss_risk],
        "explainability": {
            "value_anchor": round(float(expected_profit_after_purchase), 4),
            "risk_adjusted_value": round(float(risk_adjusted), 4),
            "system_fit_score": round(float(system_check["system_fit_score"]), 4),
            "wind_uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
            "price_role": price_role,
        },
        "working_bid": round(float(optimal_purchase_price), 4),
        "working_bid_source": working_bid_source,
        "working_bid_reason": working_reason,
        "zero_bid_reason": "" if bids["balanced"] > 0.0 else "delta-profit неположительный",
        "cap_reason": f"Предел задан break-even тарифом {evaluation.break_even_tariff:.2f}.",
        "recommended_bid_reason": working_reason,
        "max_bid_reason": f"Break-even тариф {evaluation.break_even_tariff:.2f}.",
        "net_profit_at_recommended_bid": bids["profit_at_recommended"],
        "net_profit_at_max_bid": 0.0,
        "remaining_budget_after_recommended_bid": round(float(remaining_after), 4),
        "remaining_budget_after_max_bid": round(float(max(0.0, float(budget.get("remaining_budget", 0.0)) - evaluation.break_even_tariff)), 4),
        "budget_remaining": round(float(budget.get("remaining_budget", 0.0)), 4),
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "expected_profit_after_purchase": round(float(expected_profit_after_purchase), 4),
        "topology_feasibility": topology_feasibility,
        "wind_uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
        "wind_posterior": wind_posterior,
    }

    strategy_profile = _strategy_score(
        strategy=strategy,
        evaluation=evaluation,
        candidate_objects=candidate_objects,
        system_check=system_check,
        risk_adjusted=risk_adjusted,
    )

    payload = {
        "summary_score": round(float(strategy_profile["score"]), 4),
        "base_summary_score": round(risk_adjusted, 4),
        "strategy": str(strategy_profile["strategy"]),
        "strategy_score": round(float(strategy_profile["score"]), 4),
        "strategy_reason": str(strategy_profile["reason"]),
        "analysis_stage": str(getattr(evaluation, "analysis_stage", "pre_auction_lot_valuation")),
        "forecast_context": {
            "source": forecast_context.get("source"),
            "source_label": forecast_context.get("source_label"),
            "forecast_id": forecast_context.get("forecast_id"),
            "forecast_name": forecast_context.get("forecast_name"),
            "tick_from": forecast_context.get("tick_from"),
            "tick_to": forecast_context.get("tick_to"),
            "periods_count": forecast_context.get("periods_count"),
        },
        "analysis_context": {
            "mode": "forecast",
            "source": forecast_context.get("source"),
            "forecast_id": forecast_context.get("forecast_id"),
        },
        "forecast_summary": {
            "name": forecast_context.get("forecast_name"),
            "source_kind": forecast_context.get("source"),
            "tick_from": forecast_context.get("tick_from"),
            "tick_to": forecast_context.get("tick_to"),
            "periods_count": forecast_context.get("periods_count"),
            "is_compatible": bool(compatibility.get("is_compatible", True)),
            "compatibility_report": dict(compatibility.get("compatibility_report") or {}),
        },
        "portfolio_context": {
            "bought_lots_count": int(sum(1 for row in _session_lots(session) if str(row.status or "") == "bought")),
            "cash_available": float(budget.get("cash_available", budget.get("remaining_budget", 0.0))),
            "spent_total": float(budget.get("spent_total", 0.0)),
            "remaining_budget": float(budget.get("remaining_budget", 0.0)),
            "owned_objects_count": len([row for row in session.objects if row.is_active]),
        },
        "scenario_breakdown": {
            "worst": _scenario_payload("worst", evaluation.worst_case),
            "base": _scenario_payload("base", evaluation.base_case),
            "best": _scenario_payload("best", evaluation.best_case),
        },
        "financial_breakdown": financial_breakdown,
        "decision_summary": decision_summary,
        "reasons": [working_reason, *evaluation.synergy_notes],
        "risk_commentary": bid_constraints_summary,
        "strategy_fit_text": working_reason,
        "recommended_bid": round(float(optimal_purchase_price), 4),
        "recommended_bid_hard": round(float(evaluation.break_even_tariff), 4),
        "max_bid": round(float(evaluation.break_even_tariff), 4),
        "recommended_bid_reason": working_reason,
        "max_bid_reason": f"Break-even тариф {evaluation.break_even_tariff:.2f}.",
        "cap_reason": f"Предел задан break-even тарифом {evaluation.break_even_tariff:.2f}.",
        "break_even_tariff": round(float(evaluation.break_even_tariff), 4),
        "lot_profile": str(evaluation.lot_profile),
        "price_role": price_role,
        "optimal_purchase_price": round(float(optimal_purchase_price), 4),
        "expected_profit_after_purchase": round(float(expected_profit_after_purchase), 4),
        "risk_adjusted_profit": round(float(risk_adjusted), 4),
        "direct_delta_profit": round(float(evaluation.direct_delta_profit), 4),
        "enabler_value": round(float(evaluation.enabler_value), 4),
        "bundle_synergy_value": round(float(evaluation.bundle_synergy_value), 4),
        "synergy_value": round(float(getattr(evaluation, "synergy_value", evaluation.bundle_synergy_value)), 4),
        "infrastructure_enabler_value": round(float(getattr(evaluation, "infrastructure_enabler_value", evaluation.enabler_value)), 4),
        "topology_feasibility": topology_feasibility,
        "wind_uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
        "wind_posterior": wind_posterior,
        "minimum_acceptable_tariff": round(float(evaluation.minimum_acceptable_tariff or 0.0), 4),
        "recommended_walkdown_tariff": round(float(evaluation.recommended_walkdown_tariff or 0.0), 4),
        "aggressive_floor": round(float(evaluation.aggressive_floor or 0.0), 4),
        "hard_floor": round(float(evaluation.hard_floor or 0.0), 4),
        "maximum_acceptable_service_tariff": round(float(evaluation.maximum_acceptable_service_tariff or 0.0), 4),
        "recommended_bid_ceiling": round(float(evaluation.recommended_bid_ceiling or 0.0), 4),
        "soft_ceiling": round(float(evaluation.soft_ceiling or 0.0), 4),
        "hard_ceiling": round(float(evaluation.hard_ceiling or 0.0), 4),
        "recommended_opening_bid": round(float(evaluation.recommended_opening_bid or 0.0), 4),
        "recommended_counter_bid": round(float(evaluation.recommended_counter_bid or 0.0), 4),
        "hard_limit": round(float(evaluation.hard_limit or 0.0), 4),
        "allpay_trigger_policy": str(evaluation.allpay_trigger_policy or ""),
        "if_allpay_triggered_max_cash_offer": round(
            min(allpay_remaining, max(0.0, float(evaluation.expected_delta_profit))),
            4,
        ),
        "cumulative_allpay_budget_remaining": round(float(allpay_remaining), 4),
        "recommended_bid_or_tariff": round(float(optimal_purchase_price), 4),
        "expected_delta_profit": round(float(expected_profit_after_purchase), 4),
        "best_case": {
            "delta_profit": round(float(evaluation.best_case.delta_profit), 4),
            "notes": list(evaluation.best_case.notes),
        },
        "base_case": {
            "delta_profit": round(float(evaluation.base_case.delta_profit), 4),
            "notes": list(evaluation.base_case.notes),
        },
        "worst_case": {
            "delta_profit": round(float(evaluation.worst_case.delta_profit), 4),
            "notes": list(evaluation.worst_case.notes),
        },
        "topology_risk": str(evaluation.topology_risk),
        "market_risk": str(evaluation.market_risk),
        "balancing_risk": str(evaluation.balancing_risk),
        "loss_risk": str(evaluation.loss_risk),
        "working_bid": round(float(optimal_purchase_price), 4),
        "working_bid_source": working_bid_source,
        "working_bid_reason": working_reason,
        "zero_bid_reason": "" if bids["balanced"] > 0.0 else "delta-profit неположительный",
        "cautious_bid": bids["safe"],
        "recommended_bid_safe": bids["safe"],
        "recommended_bid_balanced": bids["balanced"],
        "recommended_bid_aggressive": bids["aggressive"],
        "safe_bid": bids["safe"],
        "target_bid": round(float(optimal_purchase_price), 4),
        "hard_cap": round(float(evaluation.break_even_tariff), 4),
        "hard_ceiling_bid": round(float(evaluation.break_even_tariff), 4),
        "budget_adjusted_bid": budget_adjusted_bid,
        "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
        "is_stale": False,
        "stale_reason": "",
        "score_definition": (
            f"Summary score = risk-adjusted profit по правилам ИЭС 2026. "
            f"В основе unified optimizer: {strategy_profile['reason']}"
        ),
        "system_check": system_check,
        "metrics": {
            "forecast_compatibility": compatibility,
            "portfolio_delta": {
                "net_profit_base": round(float(expected_profit_after_purchase), 4),
                "risk_adjusted_net_profit": round(risk_adjusted, 4),
                "risk_adjusted_profit": round(risk_adjusted, 4),
                "direct_delta_profit": round(float(evaluation.direct_delta_profit), 4),
                "enabler_value": round(float(evaluation.enabler_value), 4),
                "wind_uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
                "horizon_ticks": horizon,
            },
            "delta_score": round(float(risk_adjusted), 4),
            "role_breakdown": role_breakdown,
            "synergy": {
                "score": round(float(evaluation.enabler_value + evaluation.bundle_synergy_value), 4),
                "standalone_expected_net_profit": round(float(evaluation.direct_delta_profit), 4),
                "marginal_expected_net_profit": round(float(expected_profit_after_purchase), 4),
                "bundle_synergy_value": round(float(evaluation.bundle_synergy_value), 4),
            },
            "system_check": system_check,
            "storage_value": {
                "arbitrage": round(float(evaluation.storage_value.arbitrage), 4),
                "balancing": round(float(evaluation.storage_value.balancing), 4),
                "reserve": round(float(evaluation.storage_value.reserve), 4),
                "anti_dumping_support": round(float(evaluation.storage_value.anti_dumping_support), 4),
            },
            "market": {
                "anti_dumping_scaler": float(market_cfg.get("anti_dumping_scaler", 1.2) or 1.2),
                "anti_dumping_buffer_mw": float(market_cfg.get("anti_dumping_buffer_mw", 10.0) or 10.0),
                "max_exchange_bids": int(market_cfg.get("max_exchange_bids", 100) or 100),
                "exchange_price_min": float(market_cfg.get("exchange_price_min", 2.0) or 2.0),
                "exchange_price_max": float(market_cfg.get("exchange_price_max", 20.0) or 20.0),
                "anti_dumping_formula": "1.2 * useful_energy_(t-1) + 10",
                "market_model": str(market_cfg.get("market_model", "aggregate_exchange_with_gp_fallback")),
            },
            "wind": {
                "posterior": wind_posterior,
                "uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
            },
            "strategy": strategy_profile,
            "bids": {
                "recommended_bid_safe": bids["safe"],
                "recommended_bid_balanced": bids["balanced"],
                "recommended_bid_aggressive": bids["aggressive"],
                "budget_adjusted_bid": budget_adjusted_bid,
                "recommended_bid": round(float(optimal_purchase_price), 4),
                "max_bid": round(float(evaluation.break_even_tariff), 4),
                "gross_expected_profit_before_bid": round(float(expected_profit_after_purchase), 4),
                "budget_remaining": float(budget.get("remaining_budget", 0.0)),
                "net_profit_at_recommended_bid": bids["profit_at_recommended"],
                "remaining_budget_after_recommended_bid": float(remaining_after),
                "budget_preservation_note": BUDGET_PRESERVATION_NOTE,
                "bid_constraints_summary": bid_constraints_summary,
                "valuation_model": {
                    "model": "valuation_model_v3",
                    "engine": "unified_lot_optimizer_v2",
                    "profile": "consumer" if evaluation.auction_direction == "descending_consumer_tariff" else "provider",
                    "risk_band": max(
                        evaluation.topology_risk,
                        evaluation.market_risk,
                        evaluation.balancing_risk,
                        evaluation.loss_risk,
                    ),
                    "portfolio_synergy": round(float(evaluation.enabler_value), 4),
                    "bundle_synergy_value": round(float(evaluation.bundle_synergy_value), 4),
                    "system_fit_score": round(float(system_check["system_fit_score"]), 4),
                    "anchor_value": round(float(expected_profit_after_purchase), 4),
                    "optimal_purchase_price": round(float(optimal_purchase_price), 4),
                    "price_role": price_role,
                    "expected_profit_after_purchase": round(float(expected_profit_after_purchase), 4),
                    "risk_adjusted_profit": round(float(risk_adjusted), 4),
                    "wind_uncertainty_penalty": round(float(wind_uncertainty_penalty), 4),
                    "wind_posterior": wind_posterior,
                    "cautious_bid": bids["safe"],
                    "target_bid": round(float(optimal_purchase_price), 4),
                    "hard_ceiling_bid": round(float(evaluation.break_even_tariff), 4),
                    "budget_adjusted_bid": budget_adjusted_bid,
                    "working_bid": round(float(optimal_purchase_price), 4),
                    "break_even_tariff": round(float(evaluation.break_even_tariff), 4),
                    "assumptions": list(evaluation.assumptions),
                },
            },
        },
        "explanation": evaluation.explanation,
        "lot_name": lot_name,
    }
    return payload


def _save_evaluation(
    *,
    session: GameSession,
    lot: Lot,
    payload: Dict[str, Any],
) -> None:
    db.session.query(EvaluationResult).filter_by(
        session_id=int(session.id),
        lot_id=int(lot.id),
        mode="ies_2026",
    ).delete(synchronize_session=False)
    db.session.add(
        EvaluationResult(
            session_id=int(session.id),
            lot_id=int(lot.id),
            mode="ies_2026",
            scenario="base",
            summary_score=float(payload.get("summary_score", 0.0) or 0.0),
            metrics_json=payload,
            explanation=str(payload.get("explanation", "")),
            recommended_bid_soft=float(payload.get("recommended_bid_safe", 0.0) or 0.0),
            recommended_bid_hard=float(payload.get("break_even_tariff", 0.0) or 0.0),
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
    rules_cfg = dict(session.ruleset.config_json or {})
    default_connection_point = _default_connection_point(rules_cfg)
    return {
        "forecast": selected_forecast,
        "forecast_pack": _forecast_pack_for_session(session, cast(Optional[Forecast], selected_forecast)),
        "forecast_context": dict(analysis_ctx.get("forecast_context") or {}),
        "compatibility": compatibility,
        "base_objects": _base_objects(session, default_connection_point=default_connection_point),
        "rules_cfg": rules_cfg,
        "default_connection_point": default_connection_point,
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
    del portfolio_lots, reserved_spend
    ordered_lots = sorted(list(lots), key=lambda row: int(row.id))
    if not ordered_lots:
        raise ValueError("Для bundle evaluation нужен хотя бы один лот")
    del strategy
    selected_strategy = "unified"
    context = fast_context or prepare_fast_scoring_context(
        session=session,
        forecast=forecast,
        available_lots=available_lots,
    )
    base_objects = list(context.get("base_objects") or _base_objects(session))
    forecast_pack = dict(context.get("forecast_pack") or _forecast_pack_for_session(session, forecast))
    forecast_context = dict(context.get("forecast_context") or {})
    compatibility = dict(context.get("compatibility") or _compatibility_or_error(session, forecast))
    default_connection_point = str(
        context.get("default_connection_point")
        or _default_connection_point(dict(session.ruleset.config_json or {}))
    ).upper()
    candidate_objects: List[EnergyObject] = []
    for lot in ordered_lots:
        candidate_objects.extend(
            _expanded_candidate_objects_from_lot(
                lot,
                default_connection_point=default_connection_point,
            )
        )
    follow_ups: List[Tuple[int, str, Sequence[EnergyObject]]] = []
    if len(ordered_lots) == 1:
        current_id = int(ordered_lots[0].id)
        for row in list(available_lots or context.get("available_lots") or []):
            if int(row.id) == current_id or str(row.status or "") != "available":
                continue
            follow_ups.append(
                (
                    int(row.id),
                    row.name,
                    _expanded_candidate_objects_from_lot(
                        row,
                        default_connection_point=default_connection_point,
                    ),
                )
            )
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
        strategy=selected_strategy,
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
    rows = list(lots)
    if not rows:
        return []
    fast_context = prepare_fast_scoring_context(
        session=session,
        forecast=forecast,
        available_lots=[row for row in _session_lots(session) if str(row.status or "") == "available"],
    )
    out: List[Dict[str, Any]] = []
    for lot in rows:
        payload = evaluate_lot_bundle(
            session=session,
            lots=[lot],
            strategy=strategy,
            forecast=forecast,
            available_lots=cast(Sequence[Lot], fast_context.get("available_lots") or []),
            fast_context=fast_context,
        )
        payload["lot_id"] = int(lot.id)
        out.append(payload)
        if persist:
            _save_evaluation(session=session, lot=lot, payload=payload)
    out.sort(
        key=lambda row: (
            float(((row.get("metrics") or {}).get("portfolio_delta") or {}).get("risk_adjusted_profit", row.get("summary_score", 0.0)) or 0.0),
            float(row.get("expected_profit_after_purchase", row.get("expected_delta_profit", 0.0)) or 0.0),
            -float(row.get("wind_uncertainty_penalty", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return out


def recommend_best_lot(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    ranked = rank_lots(
        session=session,
        lots=list(lots),
        strategy=strategy,
        forecast=forecast,
        persist=False,
    )
    best = ranked[0] if ranked else None
    return {
        "model": "ies_2026_delta_profit",
        "best": best,
        "best_lot": best,
        "ranked": ranked,
        "ranked_lots": ranked,
        "summary": {
            "count": len(ranked),
            "best_lot_id": int(best.get("lot_id", 0) or 0) if best else None,
            "best_expected_delta_profit": float(best.get("expected_delta_profit", 0.0) or 0.0)
            if best
            else 0.0,
        },
    }


def strategy_fit(
    *,
    session: GameSession,
    lot: Lot,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    evaluation = evaluate_lot(
        session=session,
        lot=lot,
        strategy=None,
        forecast=forecast,
        persist=False,
    )
    return {
        "model": "ies_2026_delta_profit",
        "lot_id": int(lot.id),
        "lot_name": lot.name,
        "topology_risk": str(evaluation.get("topology_risk") or "low"),
        "market_risk": str(evaluation.get("market_risk") or "low"),
        "balancing_risk": str(evaluation.get("balancing_risk") or "low"),
        "loss_risk": str(evaluation.get("loss_risk") or "low"),
        "expected_delta_profit": float(evaluation.get("expected_delta_profit", 0.0) or 0.0),
        "risk_adjusted_profit": float(evaluation.get("risk_adjusted_profit", 0.0) or 0.0),
        "optimal_purchase_price": float(evaluation.get("optimal_purchase_price", 0.0) or 0.0),
        "break_even_tariff": float(evaluation.get("break_even_tariff", 0.0) or 0.0),
        "recommended_bid_or_tariff": float(
            evaluation.get(
                "optimal_purchase_price",
                evaluation.get("recommended_bid_or_tariff", evaluation.get("recommended_bid", 0.0)),
            )
            or 0.0
        ),
        "system_check": dict(evaluation.get("system_check") or {}),
        "synergy": dict(((evaluation.get("metrics") or {}).get("synergy") or {})),
        "wind_posterior": dict(evaluation.get("wind_posterior") or {}),
        "wind_uncertainty_penalty": float(evaluation.get("wind_uncertainty_penalty", 0.0) or 0.0),
        "mounting_requirements": list(
            evaluation.get("mounting_requirements")
            or evaluation.get("system_check", {}).get("critical_blocking_errors")
            or []
        ),
        "conflicts": list(evaluation.get("conflicts") or []),
        "assumptions": list(
            (((evaluation.get("metrics") or {}).get("bids") or {}).get("valuation_model") or {}).get(
                "assumptions"
            )
            or []
        ),
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
