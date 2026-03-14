from __future__ import annotations

from typing import Dict, List, Tuple

from ies_bot_skeleton.domain.lot_analysis.types import DeltaBreakdown, Lot, ObjectItem, State
from ies_bot_skeleton.domain.lot_analysis.scoring.score import score_state


def marginal_value(
    state: State, owned_items: List[ObjectItem], lot: Lot, forecasts, cfg: Dict
) -> Tuple[DeltaBreakdown, DeltaBreakdown, DeltaBreakdown]:
    base0 = score_state(state, list(owned_items), forecasts, dict(cfg), "base")
    worst0 = score_state(state, list(owned_items), forecasts, dict(cfg), "worst")
    best0 = score_state(state, list(owned_items), forecasts, dict(cfg), "best")

    merged = list(owned_items) + list(lot.items)
    base1 = score_state(state, merged, forecasts, dict(cfg), "base")
    worst1 = score_state(state, merged, forecasts, dict(cfg), "worst")
    best1 = score_state(state, merged, forecasts, dict(cfg), "best")

    def d(a, b) -> DeltaBreakdown:
        out = DeltaBreakdown(
            delta_total=b.score_total - a.score_total,
            delta_income=b.income - a.income,
            delta_penalties=b.penalties - a.penalties,
            delta_contracts=b.contracts - a.contracts,
            delta_fuel_and_taxes=b.fuel_and_taxes - a.fuel_and_taxes,
            delta_market_net=b.market_net - a.market_net,
            delta_network_losses_cost=b.network_losses_cost - a.network_losses_cost,
            delta_eco_value=b.eco_value - a.eco_value,
            delta_risk_penalty=b.risk_penalty - a.risk_penalty,
            delta_eco_points=b.eco_points - a.eco_points,
            flags=[],
            reasons=[],
        )
        for n in b.notes:
            if (
                n.startswith("NETPLAN:")
                or n.startswith("OVERLOAD_RISK:")
                or n.startswith("WEAR_OUTAGE:")
                or n.startswith("INSTANT_")
            ):
                out.flags.append(n)
        return out

    return d(base0, base1), d(worst0, worst1), d(best0, best1)


__all__ = ["marginal_value"]
