from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

from ...common.budgeting import (
    allpay_spent_total,
    budget_snapshot,
    purchase_spent_total,
    remaining_budget as session_remaining_budget,
)
from ..models import Forecast, GameSession, Lot
from .analysis_context import resolve_analysis_context
from .evaluation import ForecastCompatibilityError, evaluate_lot_bundle


@dataclass(frozen=True)
class ComboEvaluation:
    lot_ids: Tuple[int, ...]
    payload: Dict[str, Any]
    total_price: float
    risk_adjusted_net_profit: float
    utility_score: float
    net_profit_base: float
    risk_total: float
    cautious_bid: float
    target_bid: float
    hard_ceiling_bid: float
    budget_adjusted_bid: float
    working_bid: float
    working_bid_source: str
    working_bid_reason: str
    synergy_score: float
    explanation: str
    lot_bid_breakdown: List[Dict[str, Any]]


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _lot_purchase_spent(session: GameSession) -> float:
    return purchase_spent_total(session)


def _allpay_spent(session: GameSession) -> float:
    return allpay_spent_total(session)


def _remaining_budget(session: GameSession) -> float:
    return session_remaining_budget(session)


def _portfolio_context(session: GameSession) -> Dict[str, Any]:
    budget = budget_snapshot(session)
    bought = 0
    for lot in _session_lots(session):
        if str(lot.status or "") == "bought":
            bought += 1
    return {
        "analysis_mode": "unified",
        "bought_lots_count": int(bought),
        **budget,
    }


def _combo_price(lots: Sequence[Lot]) -> float:
    return float(sum(float(lot.current_bid or 0.0) for lot in lots))


def _combo_operational_price(combo: ComboEvaluation) -> float:
    working = float(combo.working_bid or 0.0)
    if working > 0.0:
        return working
    budget_adjusted = float(combo.budget_adjusted_bid or 0.0)
    if budget_adjusted > 0.0:
        return budget_adjusted
    target = float(combo.target_bid or 0.0)
    if target > 0.0:
        return target
    return 0.0


def _objective_key(item: ComboEvaluation) -> Tuple[float, float]:
    return float(item.risk_adjusted_net_profit), float(item.utility_score)


def _presentation_key(item: ComboEvaluation) -> Tuple[float, float, float]:
    return (
        float(item.net_profit_base),
        float(item.risk_adjusted_net_profit),
        float(item.utility_score),
    )


def _stringify_reasons(payload: Dict[str, Any], *, synergy_score: float) -> str:
    reasons = list(payload.get("reasons") or [])
    metrics = dict(payload.get("metrics") or {})
    scenario_delta = dict(metrics.get("scenario_delta") or {}).get("base") or {}
    role_breakdown = dict(metrics.get("role_breakdown") or {})
    phrases: List[str] = []
    if synergy_score > 0:
        phrases.append(
            f"Положительная синергия {synergy_score:+.2f}: комбинация усиливает портфель."
        )
    elif synergy_score < 0:
        phrases.append(
            f"Отрицательная синергия {synergy_score:+.2f}: эффекты частично конфликтуют."
        )

    if float(scenario_delta.get("delta_penalties", 0.0)) < 0:
        phrases.append("Комбинация снижает штрафы за дефицит и недоотпуск.")
    if float(scenario_delta.get("delta_market_net", 0.0)) < 0:
        phrases.append("Снижается зависимость от закупки энергии на рынке.")
    if float(scenario_delta.get("delta_risk_penalty", 0.0)) < 0:
        phrases.append("Профиль снижает риск перегрузки и нестабильности.")
    if float(role_breakdown.get("infrastructure", 0.0)) > 0:
        phrases.append("Инфраструктурный вклад помогает снимать сетевые ограничения.")
    if float(role_breakdown.get("storage", 0.0)) > 0:
        phrases.append("Накопители повышают гибкость и резерв по тактам.")

    if not phrases and reasons:
        phrases.extend(str(item) for item in reasons[:3])
    if not phrases:
        phrases.append("Комбинация ранжирована по риск-скорректированной маржинальной прибыли.")
    return " ".join(phrases)


def _combo_eval(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    strategy: Optional[str],
    forecast: Optional[Forecast],
    singles_net_profit: Dict[int, float],
    standalone_bids: Dict[int, Dict[str, Any]],
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
) -> ComboEvaluation:
    ordered_lots = sorted(lots, key=lambda row: int(row.id))
    payload = evaluate_lot_bundle(
        session=session,
        lots=ordered_lots,
        strategy=strategy,
        forecast=forecast,
        portfolio_lots=portfolio_lots,
        reserved_spend=reserved_spend,
    )
    metrics = dict(payload.get("metrics") or {})
    bids = dict(metrics.get("bids") or {})
    decision_summary = dict(payload.get("decision_summary") or {})
    portfolio_delta = dict(metrics.get("portfolio_delta") or {})
    financial = dict(payload.get("financial_breakdown") or {})
    losses = dict(financial.get("losses_and_risks") or {})
    lot_ids = tuple(int(lot.id) for lot in ordered_lots)
    base_profit = float(portfolio_delta.get("net_profit_base", 0.0) or 0.0)
    single_sum = float(sum(float(singles_net_profit.get(lot_id, 0.0) or 0.0) for lot_id in lot_ids))
    synergy_score = float(base_profit - single_sum)
    explanation = _stringify_reasons(payload, synergy_score=synergy_score)

    standalone_rows: List[Dict[str, Any]] = []
    for lot in ordered_lots:
        lot_id = int(lot.id)
        base = dict(standalone_bids.get(lot_id) or {})
        standalone_target = float(base.get("target_bid", 0.0) or 0.0)
        standalone_cautious = float(base.get("cautious_bid", 0.0) or 0.0)
        standalone_hard = float(base.get("hard_ceiling_bid", 0.0) or 0.0)
        standalone_working = float(base.get("working_bid", 0.0) or 0.0)
        if len(ordered_lots) == 1 and standalone_target <= 0.0:
            standalone_target = float(bids.get("target_bid", 0.0) or 0.0)
            standalone_cautious = float(bids.get("cautious_bid", 0.0) or 0.0)
            standalone_hard = float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
            standalone_working = float(
                payload.get("working_bid") or decision_summary.get("working_bid") or 0.0
            )
        lot_price = float(lot.current_bid or 0.0)
        weight = standalone_target if standalone_target > 0.0 else lot_price
        if weight <= 0.0:
            weight = 1.0
        standalone_rows.append(
            {
                "lot_id": lot_id,
                "lot_name": lot.name,
                "lot_price": lot_price,
                "standalone_net_profit": float(singles_net_profit.get(lot_id, 0.0) or 0.0),
                "standalone_target_bid": standalone_target,
                "standalone_cautious_bid": standalone_cautious,
                "standalone_hard_ceiling_bid": standalone_hard,
                "standalone_working_bid": standalone_working,
                "weight": weight,
            }
        )

    weight_total = float(sum(float(row["weight"]) for row in standalone_rows)) or 1.0
    standalone_profit_total = float(
        sum(float(row["standalone_net_profit"]) for row in standalone_rows)
    )
    synergy_profit = float(base_profit - standalone_profit_total)
    target_synergy = float(
        float(bids.get("target_bid", 0.0) or 0.0)
        - sum(float(row["standalone_target_bid"]) for row in standalone_rows)
    )
    cautious_synergy = float(
        float(bids.get("cautious_bid", 0.0) or 0.0)
        - sum(float(row["standalone_cautious_bid"]) for row in standalone_rows)
    )
    hard_synergy = float(
        float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
        - sum(float(row["standalone_hard_ceiling_bid"]) for row in standalone_rows)
    )

    combo_target_bid = float(bids.get("target_bid", 0.0) or 0.0)
    combo_budget_adjusted = float(bids.get("budget_adjusted_bid", 0.0) or 0.0)
    combo_working_bid = float(payload.get("working_bid") or decision_summary.get("working_bid") or 0.0)
    provisional_allocations: List[Dict[str, Any]] = []
    for row in standalone_rows:
        share = float(row["weight"]) / weight_total
        allocated_target = max(
            0.0,
            float(row["standalone_target_bid"]) + target_synergy * share,
        )
        allocated_cautious = max(
            0.0,
            float(row["standalone_cautious_bid"]) + cautious_synergy * share,
        )
        allocated_hard = max(
            allocated_target,
            float(row["standalone_hard_ceiling_bid"]) + hard_synergy * share,
        )
        provisional_allocations.append(
            {
                "row": row,
                "share": float(share),
                "allocated_target": float(allocated_target),
                "allocated_cautious": float(allocated_cautious),
                "allocated_hard": float(allocated_hard),
            }
        )

    total_allocated_target = float(
        sum(float(entry["allocated_target"] or 0.0) for entry in provisional_allocations)
    )
    if len(ordered_lots) == 1:
        budget_scale = 0.0
        working_scale = 0.0
    elif total_allocated_target > 0.0:
        budget_scale = max(0.0, combo_budget_adjusted / total_allocated_target)
        working_scale = max(0.0, combo_working_bid / total_allocated_target)
    else:
        budget_scale = 0.0
        working_scale = 0.0

    lot_bid_breakdown: List[Dict[str, Any]] = []
    for entry in provisional_allocations:
        row = cast(Dict[str, Any], dict(entry.get("row") or {}))
        share = float(entry.get("share") or 0.0)
        allocated_target = float(entry.get("allocated_target") or 0.0)
        allocated_cautious = float(entry.get("allocated_cautious") or 0.0)
        allocated_hard = float(entry.get("allocated_hard") or 0.0)
        if len(ordered_lots) == 1:
            allocated_target = float(combo_target_bid)
            allocated_cautious = float(bids.get("cautious_bid", 0.0) or 0.0)
            allocated_hard = float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
            allocated_budget_adjusted = float(combo_budget_adjusted)
            allocated_working = float(combo_working_bid)
        else:
            allocated_budget_adjusted = float(max(0.0, allocated_target * budget_scale))
            allocated_working = float(max(0.0, allocated_target * working_scale))
        recommended_bid = (
            float(allocated_working)
            if float(allocated_working) > 0.0
            else float(allocated_budget_adjusted)
        )
        lot_bid_breakdown.append(
            {
                "lot_id": int(row["lot_id"]),
                "lot_name": str(row["lot_name"]),
                "lot_label": f"{row['lot_name']} ({int(row['lot_id'])})",
                "standalone_net_profit": float(row["standalone_net_profit"]),
                "standalone_target_bid": float(row["standalone_target_bid"]),
                "standalone_hard_ceiling_bid": float(row["standalone_hard_ceiling_bid"]),
                "standalone_working_bid": float(row["standalone_working_bid"]),
                "allocated_target_bid": float(allocated_target),
                "allocated_cautious_bid": float(allocated_cautious),
                "allocated_hard_ceiling_bid": float(allocated_hard),
                "allocated_working_bid": float(allocated_working),
                "synergy_allocated": float(target_synergy * share),
                "budget_adjusted_bid": float(allocated_budget_adjusted),
                "recommended_bid": float(recommended_bid),
                "allocated_net_profit": float(
                    float(row["standalone_net_profit"]) + float(synergy_profit * share)
                ),
                "price": float(recommended_bid),
                "profit": float(
                    float(row["standalone_net_profit"]) + float(synergy_profit * share)
                ),
            }
        )

    return ComboEvaluation(
        lot_ids=lot_ids,
        payload=payload,
        total_price=_combo_price(ordered_lots),
        risk_adjusted_net_profit=float(portfolio_delta.get("risk_adjusted_net_profit", 0.0) or 0.0),
        utility_score=float(payload.get("summary_score", 0.0) or 0.0),
        net_profit_base=base_profit,
        risk_total=float(losses.get("risk_total", 0.0) or 0.0),
        cautious_bid=float(bids.get("cautious_bid", 0.0) or 0.0),
        target_bid=float(bids.get("target_bid", 0.0) or 0.0),
        hard_ceiling_bid=float(bids.get("hard_ceiling_bid", 0.0) or 0.0),
        budget_adjusted_bid=float(bids.get("budget_adjusted_bid", 0.0) or 0.0),
        working_bid=float(payload.get("working_bid") or decision_summary.get("working_bid") or 0.0),
        working_bid_source=str(
            payload.get("working_bid_source")
            or decision_summary.get("working_bid_source")
            or bids.get("working_bid_source")
            or "zero"
        ),
        working_bid_reason=str(
            payload.get("working_bid_reason")
            or decision_summary.get("working_bid_reason")
            or bids.get("working_bid_reason")
            or ""
        ),
        synergy_score=synergy_score,
        explanation=explanation,
        lot_bid_breakdown=lot_bid_breakdown,
    )


def _combo_to_dict(
    combo: ComboEvaluation,
    *,
    lot_names: Dict[int, str],
    remaining_budget: float,
) -> Dict[str, Any]:
    lot_labels = [
        f"{lot_names.get(lot_id, f'Лот {lot_id}')} ({lot_id})" for lot_id in combo.lot_ids
    ]
    budget_headroom = float(remaining_budget - combo.working_bid)
    scenario_breakdown = dict(combo.payload.get("scenario_breakdown") or {})
    decision_factors = dict(combo.payload.get("decision_factors") or {})
    worst_case = dict(scenario_breakdown.get("worst") or {})
    base_case = dict(scenario_breakdown.get("base") or {})
    best_case = dict(scenario_breakdown.get("best") or {})
    return {
        "lot_ids": list(combo.lot_ids),
        "lot_names": [lot_names.get(lot_id, f"Лот {lot_id}") for lot_id in combo.lot_ids],
        "lot_labels": lot_labels,
        "display_title": " + ".join(lot_labels),
        "lots_count": len(combo.lot_ids),
        "total_price": float(combo.total_price),
        "total_profit": float(combo.net_profit_base),
        "risk_adjusted_net_profit": float(combo.risk_adjusted_net_profit),
        "net_profit_base": float(combo.net_profit_base),
        "utility": float(combo.utility_score),
        "utility_score": float(combo.utility_score),
        "risk": float(combo.risk_total),
        "synergy": float(combo.synergy_score),
        "synergy_score": float(combo.synergy_score),
        "cautious_bid": float(combo.cautious_bid),
        "target_bid": float(combo.target_bid),
        "hard_ceiling_bid": float(combo.hard_ceiling_bid),
        "budget_adjusted_bid": float(combo.budget_adjusted_bid),
        "working_bid": float(combo.working_bid),
        "working_bid_source": str(combo.working_bid_source),
        "working_bid_reason": str(combo.working_bid_reason),
        "scenario_breakdown": scenario_breakdown,
        "decision_factors": decision_factors,
        "worst_case_profit": float(worst_case.get("net_profit", 0.0) or 0.0),
        "base_case_profit": float(base_case.get("net_profit", 0.0) or 0.0),
        "best_case_profit": float(best_case.get("net_profit", 0.0) or 0.0),
        "portfolio_delta": dict((combo.payload.get("metrics") or {}).get("portfolio_delta") or {}),
        "forecast_compatibility": dict(
            (combo.payload.get("metrics") or {}).get("forecast_compatibility") or {}
        ),
        "budget_fit": {
            "is_affordable": bool(combo.working_bid <= remaining_budget + 1e-9),
            "remaining_budget": float(remaining_budget),
            "headroom": float(budget_headroom),
        },
        "reason": combo.explanation,
        "explanation": combo.explanation,
        "lot_bid_breakdown": list(combo.lot_bid_breakdown),
        "system_check": dict((combo.payload.get("metrics") or {}).get("system_check") or {}),
    }


def _within_budget(*, price: float, remaining_budget: float) -> bool:
    return float(price) <= float(remaining_budget) + 1e-9


def _build_combo_catalog(
    *,
    session: GameSession,
    available_lots: Sequence[Lot],
    strategy: Optional[str],
    forecast: Optional[Forecast],
    remaining_budget: float,
    beam_width: int,
    max_group_size: int,
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
) -> List[ComboEvaluation]:
    lot_map = {int(lot.id): lot for lot in available_lots}
    singles: List[ComboEvaluation] = []
    singles_net_profit: Dict[int, float] = {}
    standalone_bids: Dict[int, Dict[str, Any]] = {}
    for lot in available_lots:
        row = _combo_eval(
            session=session,
            lots=[lot],
            strategy=strategy,
            forecast=forecast,
            singles_net_profit={},
            standalone_bids={},
            portfolio_lots=portfolio_lots,
            reserved_spend=reserved_spend,
        )
        if not _within_budget(
            price=_combo_operational_price(row),
            remaining_budget=remaining_budget,
        ):
            continue
        singles.append(row)
        singles_net_profit[int(lot.id)] = float(row.net_profit_base)
        standalone_bids[int(lot.id)] = {
            "target_bid": float(row.target_bid),
            "cautious_bid": float(row.cautious_bid),
            "hard_ceiling_bid": float(row.hard_ceiling_bid),
            "working_bid": float(row.working_bid),
        }

    combos: Dict[Tuple[int, ...], ComboEvaluation] = {row.lot_ids: row for row in singles}
    if not singles:
        return []

    # Explore combinations incrementally from the strongest singles instead of
    # exhaustively enumerating every pair/triple on each request.
    keep_per_size = max(int(beam_width) * 3, int(beam_width), 5)
    beam = sorted(singles, key=_objective_key, reverse=True)[: max(2, int(beam_width))]
    for size in range(2, max_group_size + 1):
        expanded: Dict[Tuple[int, ...], ComboEvaluation] = {}
        for current in beam:
            used = set(current.lot_ids)
            for lot_id, lot in lot_map.items():
                if lot_id in used:
                    continue
                candidate_ids = tuple(sorted([*current.lot_ids, lot_id]))
                if len(candidate_ids) != size:
                    continue
                if candidate_ids in expanded or candidate_ids in combos:
                    continue
                candidate_lots = [lot_map[row_id] for row_id in candidate_ids]
                candidate_eval = _combo_eval(
                    session=session,
                    lots=candidate_lots,
                    strategy=strategy,
                    forecast=forecast,
                    singles_net_profit=singles_net_profit,
                    standalone_bids=standalone_bids,
                    portfolio_lots=portfolio_lots,
                    reserved_spend=reserved_spend,
                )
                if not _within_budget(
                    price=_combo_operational_price(candidate_eval),
                    remaining_budget=remaining_budget,
                ):
                    continue
                expanded[candidate_ids] = candidate_eval
        if not expanded:
            break
        ranked_expanded = sorted(expanded.values(), key=_objective_key, reverse=True)
        beam = ranked_expanded[: max(2, int(beam_width))]
        for row in ranked_expanded[:keep_per_size]:
            combos[row.lot_ids] = row

    return sorted(combos.values(), key=_objective_key, reverse=True)


def _scenario_reserved_spend(*, lots: Sequence[Lot], total_spend: float) -> float:
    market_price_total = float(sum(float(lot.current_bid or 0.0) for lot in lots))
    return max(0.0, float(total_spend) - market_price_total)


def _snapshot_sections(
    *,
    rows: Sequence[ComboEvaluation],
    lot_names: Dict[int, str],
    top_n: int,
    remaining_budget: float,
    scenario_key: str,
    scenario_title: str,
    scenario_note: str,
) -> Dict[str, Any]:
    actionable_rows = [
        row for row in rows if float(row.working_bid) > 0.0
    ]
    actionable_rows.sort(key=_presentation_key, reverse=True)
    singles = [row for row in actionable_rows if len(row.lot_ids) == 1]
    pairs = [row for row in actionable_rows if len(row.lot_ids) == 2]
    groups = [row for row in actionable_rows if len(row.lot_ids) >= 3]
    best = actionable_rows[0] if actionable_rows else None
    alternatives = actionable_rows[1:3] if len(actionable_rows) > 1 else []
    return {
        "key": scenario_key,
        "title": scenario_title,
        "note": (
            scenario_note
            if actionable_rows
            else "В этом сценарии нет комбинаций с положительной рабочей ценой."
        ),
        "best_singles": [
            _combo_to_dict(row, lot_names=lot_names, remaining_budget=remaining_budget)
            for row in singles[:top_n]
        ],
        "best_pairs": [
            _combo_to_dict(row, lot_names=lot_names, remaining_budget=remaining_budget)
            for row in pairs[:top_n]
        ],
        "best_groups": [
            _combo_to_dict(row, lot_names=lot_names, remaining_budget=remaining_budget)
            for row in groups[:top_n]
        ],
        "best_combination": (
            _combo_to_dict(best, lot_names=lot_names, remaining_budget=remaining_budget)
            if best is not None
            else None
        ),
        "alternatives": [
            _combo_to_dict(row, lot_names=lot_names, remaining_budget=remaining_budget)
            for row in alternatives
        ],
        "plan_b": (
            _combo_to_dict(alternatives[0], lot_names=lot_names, remaining_budget=remaining_budget)
            if len(alternatives) >= 1
            else None
        ),
        "plan_c": (
            _combo_to_dict(alternatives[1], lot_names=lot_names, remaining_budget=remaining_budget)
            if len(alternatives) >= 2
            else None
        ),
    }


def build_strategy_snapshot(
    *,
    session: GameSession,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    top_n: int = 5,
    beam_width: int = 7,
    max_group_size: int = 5,
) -> Dict[str, Any]:
    selected_strategy = "unified"
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast.id if forecast is not None else None
    )
    forecast = analysis_ctx["forecast"]
    forecast_summary = dict(analysis_ctx["forecast_summary"] or {})
    compatibility = dict(forecast_summary.get("compatibility_report") or {})
    if not bool(forecast_summary.get("is_compatible", True)):
        raise ForecastCompatibilityError(compatibility)

    available_lots = [lot for lot in _session_lots(session) if str(lot.status or "") == "available"]
    lot_names = {int(lot.id): lot.name for lot in available_lots}
    remaining_budget = _remaining_budget(session)

    if not available_lots:
        empty_scenario = {
            "key": "empty",
            "title": "Нет доступных лотов",
            "note": "В сессии не осталось доступных лотов для пересчёта стратегии.",
            "best_singles": [],
            "best_pairs": [],
            "best_groups": [],
            "best_combination": None,
            "alternatives": [],
            "plan_b": None,
            "plan_c": None,
        }
        return {
            "session_id": int(session.id),
            "strategy": "unified",
            "analysis_mode": "unified",
            "objective": "risk_adjusted_net_profit",
            "forecast_context": dict(analysis_ctx["forecast_context"]),
            "forecast_compatibility": compatibility,
            "portfolio_context": _portfolio_context(session),
            "budget": {"remaining_budget": float(remaining_budget)},
            "best_singles": [],
            "best_pairs": [],
            "best_groups": [],
            "best_combination": None,
            "plan_b": None,
            "plan_c": None,
            "scenarios": {
                "full_budget": dict(empty_scenario),
                "after_purchase": dict(empty_scenario),
                "after_loss": dict(empty_scenario),
            },
        }

    rows = _build_combo_catalog(
        session=session,
        available_lots=available_lots,
        strategy=selected_strategy,
        forecast=forecast,
        remaining_budget=remaining_budget,
        beam_width=max(2, int(beam_width)),
        max_group_size=max(3, int(max_group_size)),
    )
    full_budget = _snapshot_sections(
        rows=rows,
        lot_names=lot_names,
        top_n=top_n,
        remaining_budget=remaining_budget,
        scenario_key="full_budget",
        scenario_title="Полный бюджет",
        scenario_note=(
            "Единая оценка по текущему портфелю и доступному бюджету. Value-ставки не обязаны "
            "тратить весь остаток: сохранённые деньги переходят в следующие аукционы."
        ),
    )

    best_current = next((row for row in rows if float(row.working_bid) > 0.0), None)
    after_purchase_rows: List[ComboEvaluation] = []
    after_purchase_budget = remaining_budget
    if best_current is not None:
        purchased_ids = set(best_current.lot_ids)
        purchased_lots = [lot for lot in available_lots if int(lot.id) in purchased_ids]
        after_purchase_budget = max(0.0, remaining_budget - float(best_current.working_bid))
        after_purchase_rows = _build_combo_catalog(
            session=session,
            available_lots=[lot for lot in available_lots if int(lot.id) not in purchased_ids],
            strategy=selected_strategy,
            forecast=forecast,
            remaining_budget=after_purchase_budget,
            beam_width=max(2, int(beam_width)),
            max_group_size=max(3, int(max_group_size)),
            portfolio_lots=purchased_lots,
            reserved_spend=_scenario_reserved_spend(
                lots=purchased_lots,
                total_spend=float(best_current.working_bid),
            ),
        )
    after_purchase = _snapshot_sections(
        rows=after_purchase_rows,
        lot_names=lot_names,
        top_n=top_n,
        remaining_budget=after_purchase_budget,
        scenario_key="after_purchase",
        scenario_title="После покупки лучшей комбинации",
        scenario_note=(
            "Показывает, что делать следующим шагом, если лучший план уже реализован и "
            "неиспользованный остаток бюджета сохранён."
            if best_current is not None
            else "Лучшая комбинация не определена, сценарий не рассчитан."
        ),
    )

    top_single = next(
        (row for row in rows if len(row.lot_ids) == 1 and float(row.working_bid) > 0.0),
        None,
    )
    after_loss_rows: List[ComboEvaluation] = []
    excluded_lot_id = None
    if top_single is not None:
        excluded_lot_id = int(top_single.lot_ids[0])
        after_loss_rows = _build_combo_catalog(
            session=session,
            available_lots=[lot for lot in available_lots if int(lot.id) != excluded_lot_id],
            strategy=selected_strategy,
            forecast=forecast,
            remaining_budget=remaining_budget,
            beam_width=max(2, int(beam_width)),
            max_group_size=max(3, int(max_group_size)),
        )
    after_loss = _snapshot_sections(
        rows=after_loss_rows,
        lot_names=lot_names,
        top_n=top_n,
        remaining_budget=remaining_budget,
        scenario_key="after_loss",
        scenario_title="После потери лучшего лота",
        scenario_note=(
            f"Пересчёт без лота {lot_names.get(excluded_lot_id or 0, f'#{excluded_lot_id}')}"
            if excluded_lot_id is not None
            else "Лучший одиночный лот не определён, сценарий не рассчитан."
        ),
    )

    return {
        "session_id": int(session.id),
        "strategy": "unified",
        "analysis_mode": "unified",
        "objective": "risk_adjusted_net_profit",
        "forecast_context": dict(analysis_ctx["forecast_context"]),
        "forecast_compatibility": compatibility,
        "portfolio_context": _portfolio_context(session),
        "budget": {"remaining_budget": float(remaining_budget)},
        "best_singles": list(full_budget["best_singles"]),
        "best_pairs": list(full_budget["best_pairs"]),
        "best_groups": list(full_budget["best_groups"]),
        "best_combination": full_budget["best_combination"],
        "plan_b": full_budget["plan_b"],
        "plan_c": full_budget["plan_c"],
        "scenarios": {
            "full_budget": full_budget,
            "after_purchase": after_purchase,
            "after_loss": after_loss,
        },
    }


def best_pairs_for_lot(
    *,
    strategy_snapshot: Dict[str, Any],
    lot_id: int,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    target = int(lot_id)
    rows = []
    for row in strategy_snapshot.get("best_pairs") or []:
        lot_ids = [int(item) for item in row.get("lot_ids") or []]
        if target not in lot_ids:
            continue
        rows.append(dict(row))
    rows.sort(
        key=lambda item: (
            float(item.get("net_profit_base", item.get("total_profit", 0.0)) or 0.0),
            float(item.get("risk_adjusted_net_profit", 0.0) or 0.0),
            float(item.get("utility_score", item.get("utility", 0.0)) or 0.0),
        ),
        reverse=True,
    )
    return rows[:top_n]


__all__ = [
    "best_pairs_for_lot",
    "build_strategy_snapshot",
]
