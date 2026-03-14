from __future__ import annotations

from typing import Tuple

from ies_bot_skeleton.domain.lot_analysis.types import DeltaBreakdown


def summarize_delta(d: DeltaBreakdown, top_k: int = 3) -> Tuple[list, list]:
    contrib = [
        ("income", d.delta_income),
        ("penalties", -d.delta_penalties),
        ("contracts", -d.delta_contracts),
        ("fuel+tax", -d.delta_fuel_and_taxes),
        ("market", -d.delta_market_net),
        ("net-loss", -d.delta_network_losses_cost),
        ("risk", -d.delta_risk_penalty),
        ("eco", d.delta_eco_value),
    ]
    contrib.sort(key=lambda x: abs(x[1]), reverse=True)
    reasons = [f"{k}: {v:+.1f}" for k, v in contrib[:top_k] if abs(v) > 1e-6]
    return reasons, list(d.flags)


__all__ = ["summarize_delta"]
