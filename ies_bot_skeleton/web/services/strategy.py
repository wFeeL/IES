from __future__ import annotations

import copy
import hashlib
import json
import time
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
from .evaluation import (
    ForecastCompatibilityError,
    evaluate_lot_bundle,
    prepare_fast_scoring_context,
)


@dataclass(frozen=True)
class ComboEvaluation:
    lot_ids: Tuple[int, ...]
    payload: Dict[str, Any]
    total_price: float
    risk_adjusted_net_profit: float
    utility_score: float
    net_profit_base: float
    risk_total: float
    recommended_bid_safe: float
    recommended_bid_balanced: float
    recommended_bid_aggressive: float
    cautious_bid: float
    target_bid: float
    hard_ceiling_bid: float
    budget_adjusted_bid: float
    working_bid: float
    working_bid_source: str
    working_bid_reason: str
    p_win: float
    serious_competitors: int
    synergy_score: float
    explanation: str
    lot_bid_breakdown: List[Dict[str, Any]]


_STRATEGY_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_COMBO_FAST_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_SNAPSHOT_CACHE_MAX_ITEMS = 24


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


def _snapshot_fingerprint(
    *,
    session: GameSession,
    forecast: Optional[Forecast],
    top_n: int,
    beam_width: int,
    max_group_size: int,
    analysis_depth: str,
) -> str:
    lots_state = [
        {
            "id": int(lot.id),
            "status": str(lot.status or ""),
            "current_bid": float(lot.current_bid or 0.0),
            "purchase_price": (
                float(lot.purchase_price or 0.0) if lot.purchase_price is not None else None
            ),
            "available_round": int(lot.available_round or 1),
        }
        for lot in sorted(_session_lots(session), key=lambda row: int(row.id))
    ]
    objects_state = [
        {
            "id": int(obj.id),
            "type": int(obj.object_type_id),
            "parent": int(obj.parent_instance_id) if obj.parent_instance_id else None,
            "district": str(obj.district or ""),
            "active": bool(obj.is_active),
            "params": dict(obj.current_parameters_json or {}),
        }
        for obj in sorted(session.objects, key=lambda row: int(row.id))
    ]
    payload = {
        "session_id": int(session.id),
        "ruleset_id": int(session.ruleset_id),
        "forecast_id": int(forecast.id) if forecast is not None else None,
        "budget_total": float(session.budget_total or 0.0),
        "allpay_spent": float(session.allpay_spent or 0.0),
        "top_n": int(top_n),
        "beam_width": int(beam_width),
        "max_group_size": int(max_group_size),
        "analysis_depth": str(analysis_depth),
        "rules_cfg": dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {}),
        "lots": lots_state,
        "objects": objects_state,
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _snapshot_cache_get(*, key: str, ttl_seconds: float) -> Optional[Dict[str, Any]]:
    cached = _STRATEGY_SNAPSHOT_CACHE.get(str(key))
    if not cached:
        return None
    created_at = float(cached.get("_cached_at", 0.0) or 0.0)
    if created_at <= 0.0 or (time.time() - created_at) > float(max(1.0, ttl_seconds)):
        _STRATEGY_SNAPSHOT_CACHE.pop(str(key), None)
        return None
    payload = dict(cached.get("payload") or {})
    return copy.deepcopy(payload)


def _snapshot_cache_put(*, key: str, payload: Dict[str, Any]) -> None:
    _STRATEGY_SNAPSHOT_CACHE[str(key)] = {
        "_cached_at": time.time(),
        "payload": copy.deepcopy(dict(payload or {})),
    }
    if len(_STRATEGY_SNAPSHOT_CACHE) <= _SNAPSHOT_CACHE_MAX_ITEMS:
        return
    oldest_key = min(
        _STRATEGY_SNAPSHOT_CACHE.keys(),
        key=lambda cache_key: float(
            _STRATEGY_SNAPSHOT_CACHE[cache_key].get("_cached_at", 0.0) or 0.0
        ),
    )
    _STRATEGY_SNAPSHOT_CACHE.pop(oldest_key, None)


def _lot_pre_score(lot: Lot) -> float:
    role_weight = {
        "generator": 16.0,
        "consumer": 12.0,
        "storage": 11.0,
        "infrastructure": 9.0,
    }
    score = 0.0
    for item in list(getattr(lot, "items", []) or []):
        category = str(getattr(getattr(item, "object_type", None), "category", "") or "").lower()
        qty = max(1, int(getattr(item, "quantity", 1) or 1))
        score += float(role_weight.get(category, 8.0)) * float(qty)
    # Fallback for synthetic rows in unit tests and partially loaded lots.
    if score <= 0.0:
        score = 10.0
    return float(score - max(0.0, float(lot.current_bid or 0.0)) * 0.05)


def _combo_fast_context(
    *,
    session: GameSession,
    forecast: Optional[Forecast],
    portfolio_lots: Sequence[Lot] | None,
    reserved_spend: float,
) -> Dict[str, Any]:
    available_lots = [lot for lot in _session_lots(session) if str(lot.status or "") == "available"]
    key_payload = {
        "session_id": int(session.id),
        "forecast_id": int(forecast.id) if forecast is not None else None,
        "portfolio_lots": [
            int(lot.id) for lot in sorted(list(portfolio_lots or []), key=lambda row: int(row.id))
        ],
        "reserved_spend": round(float(reserved_spend), 4),
        "available_lots": [
            int(lot.id) for lot in sorted(available_lots, key=lambda row: int(row.id))
        ],
    }
    key = hashlib.sha1(
        json.dumps(key_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    cached = _COMBO_FAST_CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached
    out = prepare_fast_scoring_context(
        session=session,
        forecast=forecast,
        portfolio_lots=portfolio_lots,
        reserved_spend=reserved_spend,
        available_lots=available_lots,
    )
    _COMBO_FAST_CONTEXT_CACHE[key] = out
    return out


def _stringify_reasons(payload: Dict[str, Any], *, synergy_score: float) -> str:
    reasons = list(payload.get("reasons") or [])
    metrics = dict(payload.get("metrics") or {})
    scenario_delta = dict(metrics.get("scenario_delta") or {}).get("base") or {}
    role_breakdown = dict(metrics.get("role_breakdown") or {})
    phrases: List[str] = []
    if float(role_breakdown.get("generator", 0.0)) > 0:
        phrases.append("усиливает генерацию")
    if float(role_breakdown.get("storage", 0.0)) > 0:
        phrases.append("добавляет резерв и гибкость")
    if float(role_breakdown.get("infrastructure", 0.0)) > 0:
        phrases.append("улучшает интеграцию объектов")
    if float(scenario_delta.get("delta_market_net", 0.0)) < 0:
        phrases.append("снижает зависимость от рынка")
    if float(scenario_delta.get("delta_penalties", 0.0)) < 0:
        phrases.append("закрывает штрафной риск")
    if float(scenario_delta.get("delta_risk_penalty", 0.0)) < 0:
        phrases.append("держит риск под контролем")
    if synergy_score > 0.25:
        phrases.append("есть положительная синергия")
    elif synergy_score < -0.25:
        phrases.append("есть конфликт синергии")

    if not phrases and reasons:
        phrases.extend(str(item).lower() for item in reasons[:2])
    if not phrases:
        phrases.append("стабильная комбинация в рамках бюджета")
    compact = ", ".join(phrases[:4]).strip().strip(",")
    return (compact[:170] + "…") if len(compact) > 170 else compact


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
    fast_context = _combo_fast_context(
        session=session,
        forecast=forecast,
        portfolio_lots=portfolio_lots,
        reserved_spend=reserved_spend,
    )
    payload = evaluate_lot_bundle(
        session=session,
        lots=ordered_lots,
        strategy=strategy,
        forecast=forecast,
        portfolio_lots=portfolio_lots,
        reserved_spend=reserved_spend,
        available_lots=[
            lot for lot in _session_lots(session) if str(getattr(lot, "status", "")) == "available"
        ],
        fast_context=fast_context,
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
        standalone_balanced = float(
            base.get("recommended_bid_balanced", base.get("target_bid", 0.0)) or 0.0
        )
        standalone_safe = float(
            base.get("recommended_bid_safe", base.get("cautious_bid", 0.0)) or 0.0
        )
        standalone_aggressive = float(
            base.get("recommended_bid_aggressive", base.get("target_bid", 0.0)) or 0.0
        )
        standalone_hard = float(base.get("hard_ceiling_bid", 0.0) or 0.0)
        standalone_working = float(base.get("working_bid", 0.0) or 0.0)
        if len(ordered_lots) == 1 and standalone_balanced <= 0.0:
            standalone_balanced = float(
                bids.get("recommended_bid_balanced", bids.get("target_bid", 0.0)) or 0.0
            )
            standalone_safe = float(
                bids.get("recommended_bid_safe", bids.get("cautious_bid", 0.0)) or 0.0
            )
            standalone_aggressive = float(
                bids.get("recommended_bid_aggressive", bids.get("target_bid", 0.0)) or 0.0
            )
            standalone_hard = float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
            standalone_working = float(
                payload.get("working_bid") or decision_summary.get("working_bid") or 0.0
            )
        lot_price = float(lot.current_bid or 0.0)
        weight = standalone_balanced if standalone_balanced > 0.0 else lot_price
        if weight <= 0.0:
            weight = 1.0
        standalone_rows.append(
            {
                "lot_id": lot_id,
                "lot_name": lot.name,
                "lot_price": lot_price,
                "standalone_net_profit": float(singles_net_profit.get(lot_id, 0.0) or 0.0),
                "standalone_balanced_bid": standalone_balanced,
                "standalone_safe_bid": standalone_safe,
                "standalone_aggressive_bid": standalone_aggressive,
                "standalone_target_bid": standalone_balanced,
                "standalone_cautious_bid": standalone_safe,
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
    balanced_synergy = float(
        float(bids.get("recommended_bid_balanced", bids.get("target_bid", 0.0)) or 0.0)
        - sum(float(row["standalone_balanced_bid"]) for row in standalone_rows)
    )
    safe_synergy = float(
        float(bids.get("recommended_bid_safe", bids.get("cautious_bid", 0.0)) or 0.0)
        - sum(float(row["standalone_safe_bid"]) for row in standalone_rows)
    )
    aggressive_synergy = float(
        float(bids.get("recommended_bid_aggressive", bids.get("target_bid", 0.0)) or 0.0)
        - sum(float(row["standalone_aggressive_bid"]) for row in standalone_rows)
    )
    hard_synergy = float(
        float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
        - sum(float(row["standalone_hard_ceiling_bid"]) for row in standalone_rows)
    )

    combo_balanced_bid = float(
        bids.get("recommended_bid_balanced", bids.get("target_bid", 0.0)) or 0.0
    )
    combo_safe_bid = float(bids.get("recommended_bid_safe", bids.get("cautious_bid", 0.0)) or 0.0)
    combo_aggressive_bid = float(
        bids.get("recommended_bid_aggressive", bids.get("target_bid", 0.0)) or 0.0
    )
    combo_budget_adjusted = float(bids.get("budget_adjusted_bid", 0.0) or 0.0)
    combo_working_bid = float(
        payload.get("working_bid") or decision_summary.get("working_bid") or 0.0
    )
    provisional_allocations: List[Dict[str, Any]] = []
    for row in standalone_rows:
        share = float(row["weight"]) / weight_total
        allocated_balanced = max(
            0.0,
            float(row["standalone_balanced_bid"]) + balanced_synergy * share,
        )
        allocated_safe = max(
            0.0,
            float(row["standalone_safe_bid"]) + safe_synergy * share,
        )
        allocated_aggressive = max(
            allocated_balanced,
            float(row["standalone_aggressive_bid"]) + aggressive_synergy * share,
        )
        allocated_hard = max(
            allocated_aggressive,
            float(row["standalone_hard_ceiling_bid"]) + hard_synergy * share,
        )
        provisional_allocations.append(
            {
                "row": row,
                "share": float(share),
                "allocated_balanced": float(allocated_balanced),
                "allocated_safe": float(allocated_safe),
                "allocated_aggressive": float(allocated_aggressive),
                "allocated_hard": float(allocated_hard),
            }
        )

    total_allocated_balanced = float(
        sum(float(entry["allocated_balanced"] or 0.0) for entry in provisional_allocations)
    )
    if len(ordered_lots) == 1:
        budget_scale = 0.0
        working_scale = 0.0
    elif total_allocated_balanced > 0.0:
        budget_scale = max(0.0, combo_budget_adjusted / total_allocated_balanced)
        working_scale = max(0.0, combo_working_bid / total_allocated_balanced)
    else:
        budget_scale = 0.0
        working_scale = 0.0

    lot_bid_breakdown: List[Dict[str, Any]] = []
    for entry in provisional_allocations:
        row = cast(Dict[str, Any], dict(entry.get("row") or {}))
        share = float(entry.get("share") or 0.0)
        allocated_balanced = float(entry.get("allocated_balanced") or 0.0)
        allocated_safe = float(entry.get("allocated_safe") or 0.0)
        allocated_aggressive = float(entry.get("allocated_aggressive") or 0.0)
        allocated_hard = float(entry.get("allocated_hard") or 0.0)
        if len(ordered_lots) == 1:
            allocated_balanced = float(combo_balanced_bid)
            allocated_safe = float(combo_safe_bid)
            allocated_aggressive = float(combo_aggressive_bid)
            allocated_hard = float(bids.get("hard_ceiling_bid", 0.0) or 0.0)
            allocated_budget_adjusted = float(combo_budget_adjusted)
            allocated_working = float(combo_working_bid)
        else:
            allocated_budget_adjusted = float(max(0.0, allocated_balanced * budget_scale))
            allocated_working = float(max(0.0, allocated_balanced * working_scale))
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
                "standalone_safe_bid": float(row["standalone_safe_bid"]),
                "standalone_balanced_bid": float(row["standalone_balanced_bid"]),
                "standalone_aggressive_bid": float(row["standalone_aggressive_bid"]),
                "standalone_hard_ceiling_bid": float(row["standalone_hard_ceiling_bid"]),
                "standalone_working_bid": float(row["standalone_working_bid"]),
                "allocated_target_bid": float(allocated_balanced),
                "allocated_cautious_bid": float(allocated_safe),
                "allocated_safe_bid": float(allocated_safe),
                "allocated_balanced_bid": float(allocated_balanced),
                "allocated_aggressive_bid": float(allocated_aggressive),
                "allocated_hard_ceiling_bid": float(allocated_hard),
                "allocated_working_bid": float(allocated_working),
                "synergy_allocated": float(balanced_synergy * share),
                "budget_adjusted_bid": float(allocated_budget_adjusted),
                "recommended_bid": float(recommended_bid),
                "recommended_bid_safe": float(allocated_safe),
                "recommended_bid_balanced": float(allocated_balanced),
                "recommended_bid_aggressive": float(allocated_aggressive),
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
        recommended_bid_safe=float(
            bids.get("recommended_bid_safe", bids.get("cautious_bid", 0.0)) or 0.0
        ),
        recommended_bid_balanced=float(
            bids.get("recommended_bid_balanced", bids.get("target_bid", 0.0)) or 0.0
        ),
        recommended_bid_aggressive=float(
            bids.get("recommended_bid_aggressive", bids.get("target_bid", 0.0)) or 0.0
        ),
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
        p_win=float(
            payload.get("p_win") or decision_summary.get("p_win") or bids.get("p_win") or 0.0
        ),
        serious_competitors=int(
            payload.get("serious_competitors")
            or decision_summary.get("serious_competitors")
            or bids.get("serious_competitors")
            or 0
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
        "recommended_bid_safe": float(combo.recommended_bid_safe),
        "recommended_bid_balanced": float(combo.recommended_bid_balanced),
        "recommended_bid_aggressive": float(combo.recommended_bid_aggressive),
        "hard_ceiling_bid": float(combo.hard_ceiling_bid),
        "budget_adjusted_bid": float(combo.budget_adjusted_bid),
        "working_bid": float(combo.working_bid),
        "working_bid_source": str(combo.working_bid_source),
        "working_bid_reason": str(combo.working_bid_reason),
        "p_win": float(combo.p_win),
        "serious_competitors": int(combo.serious_competitors),
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
    stats: Dict[str, int] | None = None,
) -> List[ComboEvaluation]:
    counters = stats if stats is not None else {}
    counters.setdefault("combo_eval_calls", 0)
    counters.setdefault("pruned_by_budget", 0)
    counters.setdefault("pruned_by_upper_bound", 0)
    counters.setdefault("pruned_by_seed_cap", 0)

    max_seed_lots = max(int(beam_width) * 5, 12)
    ranked_candidates = sorted(available_lots, key=_lot_pre_score, reverse=True)
    if len(ranked_candidates) > max_seed_lots:
        counters["pruned_by_seed_cap"] = int(counters.get("pruned_by_seed_cap", 0)) + (
            len(ranked_candidates) - max_seed_lots
        )
        ranked_candidates = ranked_candidates[:max_seed_lots]
    lot_map = {int(lot.id): lot for lot in ranked_candidates}
    singles: List[ComboEvaluation] = []
    singles_net_profit: Dict[int, float] = {}
    standalone_bids: Dict[int, Dict[str, Any]] = {}
    for lot in ranked_candidates:
        counters["combo_eval_calls"] = int(counters.get("combo_eval_calls", 0)) + 1
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
            counters["pruned_by_budget"] = int(counters.get("pruned_by_budget", 0)) + 1
            continue
        singles.append(row)
        singles_net_profit[int(lot.id)] = float(row.net_profit_base)
        standalone_bids[int(lot.id)] = {
            "target_bid": float(row.target_bid),
            "cautious_bid": float(row.cautious_bid),
            "recommended_bid_safe": float(row.recommended_bid_safe),
            "recommended_bid_balanced": float(row.recommended_bid_balanced),
            "recommended_bid_aggressive": float(row.recommended_bid_aggressive),
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
    max_single_profit = max(float(row.net_profit_base) for row in singles) if singles else 0.0
    for size in range(2, max_group_size + 1):
        expanded: Dict[Tuple[int, ...], ComboEvaluation] = {}
        objective_baseline = sorted(combos.values(), key=_objective_key, reverse=True)
        min_keep_objective = (
            float(
                objective_baseline[
                    min(len(objective_baseline), keep_per_size) - 1
                ].risk_adjusted_net_profit
            )
            if objective_baseline
            else float("-inf")
        )
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
                optimistic = float(
                    sum(float(singles_net_profit.get(idx, 0.0) or 0.0) for idx in candidate_ids)
                )
                optimistic += max(0.0, float(size - 1) * max_single_profit * 0.12)
                if optimistic + 1e-9 < min_keep_objective:
                    counters["pruned_by_upper_bound"] = (
                        int(counters.get("pruned_by_upper_bound", 0)) + 1
                    )
                    continue
                candidate_lots = [lot_map[row_id] for row_id in candidate_ids]
                counters["combo_eval_calls"] = int(counters.get("combo_eval_calls", 0)) + 1
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
                    counters["pruned_by_budget"] = int(counters.get("pruned_by_budget", 0)) + 1
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
    actionable_rows = [row for row in rows if float(row.working_bid) > 0.0]
    actionable_rows.sort(key=_presentation_key, reverse=True)
    singles = [row for row in actionable_rows if len(row.lot_ids) == 1]
    pairs = [row for row in actionable_rows if len(row.lot_ids) == 2]
    groups = [row for row in actionable_rows if len(row.lot_ids) >= 3]
    best = actionable_rows[0] if actionable_rows else None
    alternatives = actionable_rows[1:3] if len(actionable_rows) > 1 else []
    return {
        "key": scenario_key,
        "title": scenario_title,
        "is_advisory": True,
        "is_exact_plan": False,
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


def _placeholder_scenario(
    *,
    scenario_key: str,
    scenario_title: str,
    scenario_note: str,
) -> Dict[str, Any]:
    return {
        "key": scenario_key,
        "title": scenario_title,
        "is_advisory": True,
        "is_exact_plan": False,
        "note": scenario_note,
        "best_singles": [],
        "best_pairs": [],
        "best_groups": [],
        "best_combination": None,
        "alternatives": [],
        "plan_b": None,
        "plan_c": None,
    }


def build_strategy_snapshot(
    *,
    session: GameSession,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    top_n: int = 5,
    beam_width: int = 7,
    max_group_size: int = 5,
    force: bool = False,
    cache_ttl_seconds: float = 120.0,
) -> Dict[str, Any]:
    selected_strategy = "unified"
    _COMBO_FAST_CONTEXT_CACHE.clear()
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast.id if forecast is not None else None
    )
    forecast = analysis_ctx["forecast"]
    forecast_summary = dict(analysis_ctx["forecast_summary"] or {})
    compatibility = dict(forecast_summary.get("compatibility_report") or {})
    if not bool(forecast_summary.get("is_compatible", True)):
        raise ForecastCompatibilityError(compatibility)

    available_lots = [lot for lot in _session_lots(session) if str(lot.status or "") == "available"]
    analysis_depth = "deep" if force else "fast"
    effective_beam_width = max(2, int(beam_width))
    effective_group_size = max(2, int(max_group_size))
    if not force:
        if len(available_lots) >= 18:
            effective_beam_width = min(effective_beam_width, 3)
            effective_group_size = min(effective_group_size, 2)
        elif len(available_lots) >= 12:
            effective_beam_width = min(effective_beam_width, 4)
            effective_group_size = min(effective_group_size, 3)
        else:
            effective_beam_width = min(effective_beam_width, 5)
            effective_group_size = min(effective_group_size, 3)
    else:
        effective_beam_width = min(effective_beam_width, 8)
        effective_group_size = min(effective_group_size, 4)

    fingerprint = _snapshot_fingerprint(
        session=session,
        forecast=forecast,
        top_n=int(top_n),
        beam_width=int(effective_beam_width),
        max_group_size=int(effective_group_size),
        analysis_depth=analysis_depth,
    )
    if not force:
        cached = _snapshot_cache_get(key=fingerprint, ttl_seconds=float(cache_ttl_seconds))
        if cached is not None:
            cached["cache"] = {
                "hit": True,
                "fingerprint": fingerprint,
                "ttl_seconds": float(cache_ttl_seconds),
            }
            return cached

    lot_names = {int(lot.id): lot.name for lot in available_lots}
    remaining_budget = _remaining_budget(session)
    compute_stats: Dict[str, int] = {}

    if not available_lots:
        empty_scenario = _placeholder_scenario(
            scenario_key="empty",
            scenario_title="Нет доступных лотов",
            scenario_note="В сессии не осталось доступных лотов для пересчёта стратегии.",
        )
        return {
            "session_id": int(session.id),
            "strategy": "unified",
            "analysis_mode": "unified",
            "objective": "risk_adjusted_net_profit",
            "snapshot_kind": "what_if_advisory",
            "analysis_depth": analysis_depth,
            "is_advisory": True,
            "is_exact_plan": False,
            "disclaimer": (
                "Стратегическая справка — это сценарный обзор комбинаций и бюджетных "
                "состояний, а не точный пошаговый план последовательного аукциона."
            ),
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
            "compute_stats": {
                **compute_stats,
                "analysis_depth": analysis_depth,
                "effective_beam_width": int(effective_beam_width),
                "effective_group_size": int(effective_group_size),
                "available_lots_count": int(len(available_lots)),
            },
            "cache": {
                "hit": False,
                "fingerprint": fingerprint,
                "ttl_seconds": float(cache_ttl_seconds),
            },
        }

    rows = _build_combo_catalog(
        session=session,
        available_lots=available_lots,
        strategy=selected_strategy,
        forecast=forecast,
        remaining_budget=remaining_budget,
        beam_width=int(effective_beam_width),
        max_group_size=int(effective_group_size),
        stats=compute_stats,
    )
    full_budget = _snapshot_sections(
        rows=rows,
        lot_names=lot_names,
        top_n=top_n,
        remaining_budget=remaining_budget,
        scenario_key="full_budget",
        scenario_title="Что если играть от текущего бюджета",
        scenario_note=(
            "Сценарный обзор по текущему портфелю и доступному бюджету. Комбинации не "
            "являются точным пошаговым планом последовательного аукциона; для живых "
            "решений используйте таблицу текущих лотов и быстрый аукцион."
        ),
    )

    best_current = next((row for row in rows if float(row.working_bid) > 0.0), None)
    after_purchase_rows: List[ComboEvaluation] = []
    after_purchase_budget = remaining_budget
    can_compute_followups = bool(force or len(available_lots) <= 10)
    if best_current is not None and can_compute_followups:
        purchased_ids = set(best_current.lot_ids)
        purchased_lots = [lot for lot in available_lots if int(lot.id) in purchased_ids]
        after_purchase_budget = max(0.0, remaining_budget - float(best_current.working_bid))
        after_purchase_rows = _build_combo_catalog(
            session=session,
            available_lots=[lot for lot in available_lots if int(lot.id) not in purchased_ids],
            strategy=selected_strategy,
            forecast=forecast,
            remaining_budget=after_purchase_budget,
            beam_width=int(effective_beam_width),
            max_group_size=int(effective_group_size),
            portfolio_lots=purchased_lots,
            reserved_spend=_scenario_reserved_spend(
                lots=purchased_lots,
                total_spend=float(best_current.working_bid),
            ),
            stats=compute_stats,
        )
    after_purchase = (
        _snapshot_sections(
            rows=after_purchase_rows,
            lot_names=lot_names,
            top_n=top_n,
            remaining_budget=after_purchase_budget,
            scenario_key="after_purchase",
            scenario_title="Что если лучший ход уже куплен",
            scenario_note=(
                "Глубокий сценарный пересчёт после покупки текущего лучшего набора."
                if best_current is not None
                else "Лучшая комбинация не определена, сценарий не рассчитан."
            ),
        )
        if best_current is not None and can_compute_followups
        else _placeholder_scenario(
            scenario_key="after_purchase",
            scenario_title="Что если лучший ход уже куплен",
            scenario_note=(
                "Сценарий скрыт в быстром режиме, чтобы не тормозить основной UX. "
                "Запустите глубокий пересчёт для подробного сценария."
                if best_current is not None
                else "Лучшая комбинация не определена, сценарий не рассчитан."
            ),
        )
    )

    top_single = next(
        (row for row in rows if len(row.lot_ids) == 1 and float(row.working_bid) > 0.0),
        None,
    )
    after_loss_rows: List[ComboEvaluation] = []
    excluded_lot_id = None
    if top_single is not None and can_compute_followups:
        excluded_lot_id = int(top_single.lot_ids[0])
        after_loss_rows = _build_combo_catalog(
            session=session,
            available_lots=[lot for lot in available_lots if int(lot.id) != excluded_lot_id],
            strategy=selected_strategy,
            forecast=forecast,
            remaining_budget=remaining_budget,
            beam_width=int(effective_beam_width),
            max_group_size=int(effective_group_size),
            stats=compute_stats,
        )
    after_loss = (
        _snapshot_sections(
            rows=after_loss_rows,
            lot_names=lot_names,
            top_n=top_n,
            remaining_budget=remaining_budget,
            scenario_key="after_loss",
            scenario_title="Что если лучший одиночный лот уйдёт",
            scenario_note=(
                f"Глубокий сценарный пересчёт без лота {lot_names.get(excluded_lot_id or 0, f'#{excluded_lot_id}')}"
                if excluded_lot_id is not None
                else "Лучший одиночный лот не определён, сценарий не рассчитан."
            ),
        )
        if top_single is not None and can_compute_followups
        else _placeholder_scenario(
            scenario_key="after_loss",
            scenario_title="Что если лучший одиночный лот уйдёт",
            scenario_note=(
                "Сценарий скрыт в быстром режиме, чтобы не гонять тяжёлый перебор на каждый просмотр."
                if top_single is not None
                else "Лучший одиночный лот не определён, сценарий не рассчитан."
            ),
        )
    )

    out = {
        "session_id": int(session.id),
        "strategy": "unified",
        "analysis_mode": "unified",
        "objective": "risk_adjusted_net_profit",
        "snapshot_kind": "what_if_advisory",
        "analysis_depth": analysis_depth,
        "is_advisory": True,
        "is_exact_plan": False,
        "disclaimer": (
            "Стратегическая справка — это сценарный обзор комбинаций. Он полезен для "
            "ориентира, но не равен точному последовательному плану покупок на живом аукционе."
        ),
        "fast_ranking_source": "lots_analytics",
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
        "compute_stats": {
            **compute_stats,
            "analysis_depth": analysis_depth,
            "effective_beam_width": int(effective_beam_width),
            "effective_group_size": int(effective_group_size),
            "available_lots_count": int(len(available_lots)),
            "followup_scenarios_computed": bool(can_compute_followups),
        },
        "cache": {
            "hit": False,
            "fingerprint": fingerprint,
            "ttl_seconds": float(cache_ttl_seconds),
        },
    }
    _snapshot_cache_put(key=fingerprint, payload=out)
    return out


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
