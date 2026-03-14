from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

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
    budget_limited_bid: float
    synergy_score: float
    explanation: str


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _remaining_budget(session: GameSession) -> float:
    spent = 0.0
    for lot in _session_lots(session):
        if str(lot.status or "") == "bought":
            spent += float(lot.purchase_price or 0.0)
    return max(0.0, float(session.budget_total or 0.0) - spent)


def _portfolio_context(session: GameSession) -> Dict[str, Any]:
    spent = 0.0
    bought = 0
    for lot in _session_lots(session):
        if str(lot.status or "") == "bought":
            bought += 1
            spent += float(lot.purchase_price or 0.0)
    return {
        "bought_lots_count": int(bought),
        "spent_total": float(spent),
        "remaining_budget": float(max(0.0, float(session.budget_total or 0.0) - spent)),
        "budget_total": float(session.budget_total or 0.0),
    }


def _combo_price(lots: Sequence[Lot]) -> float:
    return float(sum(float(lot.current_bid or 0.0) for lot in lots))


def _objective_key(item: ComboEvaluation) -> Tuple[float, float]:
    return float(item.risk_adjusted_net_profit), float(item.utility_score)


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
) -> ComboEvaluation:
    ordered_lots = sorted(lots, key=lambda row: int(row.id))
    payload = evaluate_lot_bundle(
        session=session, lots=ordered_lots, strategy=strategy, forecast=forecast
    )
    metrics = dict(payload.get("metrics") or {})
    bids = dict(metrics.get("bids") or {})
    portfolio_delta = dict(metrics.get("portfolio_delta") or {})
    financial = dict(payload.get("financial_breakdown") or {})
    losses = dict(financial.get("losses_and_risks") or {})
    lot_ids = tuple(int(lot.id) for lot in ordered_lots)
    base_profit = float(portfolio_delta.get("net_profit_base", 0.0) or 0.0)
    single_sum = float(sum(float(singles_net_profit.get(lot_id, 0.0) or 0.0) for lot_id in lot_ids))
    synergy_score = float(base_profit - single_sum)
    explanation = _stringify_reasons(payload, synergy_score=synergy_score)
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
        budget_limited_bid=float(bids.get("budget_limited_bid", 0.0) or 0.0),
        synergy_score=synergy_score,
        explanation=explanation,
    )


def _combo_to_dict(
    combo: ComboEvaluation,
    *,
    lot_names: Dict[int, str],
) -> Dict[str, Any]:
    return {
        "lot_ids": list(combo.lot_ids),
        "lot_names": [lot_names.get(lot_id, f"Лот {lot_id}") for lot_id in combo.lot_ids],
        "lots_count": len(combo.lot_ids),
        "total_price": float(combo.total_price),
        "risk_adjusted_net_profit": float(combo.risk_adjusted_net_profit),
        "net_profit_base": float(combo.net_profit_base),
        "utility_score": float(combo.utility_score),
        "risk": float(combo.risk_total),
        "synergy_score": float(combo.synergy_score),
        "cautious_bid": float(combo.cautious_bid),
        "target_bid": float(combo.target_bid),
        "hard_ceiling_bid": float(combo.hard_ceiling_bid),
        "budget_limited_bid": float(combo.budget_limited_bid),
        "scenario_breakdown": dict(combo.payload.get("scenario_breakdown") or {}),
        "portfolio_delta": dict((combo.payload.get("metrics") or {}).get("portfolio_delta") or {}),
        "forecast_compatibility": dict(
            (combo.payload.get("metrics") or {}).get("forecast_compatibility") or {}
        ),
        "reason": combo.explanation,
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
) -> List[ComboEvaluation]:
    lot_map = {int(lot.id): lot for lot in available_lots}
    singles: List[ComboEvaluation] = []
    singles_net_profit: Dict[int, float] = {}
    for lot in available_lots:
        if not _within_budget(price=_combo_price([lot]), remaining_budget=remaining_budget):
            continue
        row = _combo_eval(
            session=session,
            lots=[lot],
            strategy=strategy,
            forecast=forecast,
            singles_net_profit={},
        )
        singles.append(row)
        singles_net_profit[int(lot.id)] = float(row.net_profit_base)

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
                price = _combo_price(candidate_lots)
                if not _within_budget(price=price, remaining_budget=remaining_budget):
                    continue
                expanded[candidate_ids] = _combo_eval(
                    session=session,
                    lots=candidate_lots,
                    strategy=strategy,
                    forecast=forecast,
                    singles_net_profit=singles_net_profit,
                )
        if not expanded:
            break
        ranked_expanded = sorted(expanded.values(), key=_objective_key, reverse=True)
        beam = ranked_expanded[: max(2, int(beam_width))]
        for row in ranked_expanded[:keep_per_size]:
            combos[row.lot_ids] = row

    return sorted(combos.values(), key=_objective_key, reverse=True)


def build_strategy_snapshot(
    *,
    session: GameSession,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    top_n: int = 5,
    beam_width: int = 7,
    max_group_size: int = 5,
) -> Dict[str, Any]:
    selected_strategy = strategy or session.selected_strategy
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
        return {
            "session_id": int(session.id),
            "strategy": selected_strategy,
            "objective": "risk_adjusted_net_profit",
            "forecast_context": dict(analysis_ctx["forecast_context"]),
            "forecast_compatibility": compatibility,
            "portfolio_context": _portfolio_context(session),
            "budget": {"remaining_budget": float(remaining_budget)},
            "best_singles": [],
            "best_pairs": [],
            "best_groups": [],
            "best_combination": None,
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
    singles = [row for row in rows if len(row.lot_ids) == 1]
    pairs = [row for row in rows if len(row.lot_ids) == 2]
    groups = [row for row in rows if len(row.lot_ids) >= 3]
    best = rows[0] if rows else None

    return {
        "session_id": int(session.id),
        "strategy": selected_strategy,
        "objective": "risk_adjusted_net_profit",
        "forecast_context": dict(analysis_ctx["forecast_context"]),
        "forecast_compatibility": compatibility,
        "portfolio_context": _portfolio_context(session),
        "budget": {"remaining_budget": float(remaining_budget)},
        "best_singles": [_combo_to_dict(row, lot_names=lot_names) for row in singles[:top_n]],
        "best_pairs": [_combo_to_dict(row, lot_names=lot_names) for row in pairs[:top_n]],
        "best_groups": [_combo_to_dict(row, lot_names=lot_names) for row in groups[:top_n]],
        "best_combination": _combo_to_dict(best, lot_names=lot_names) if best is not None else None,
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
            float(item.get("synergy_score", 0.0) or 0.0),
            float(item.get("risk_adjusted_net_profit", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return rows[:top_n]


__all__ = [
    "best_pairs_for_lot",
    "build_strategy_snapshot",
]
