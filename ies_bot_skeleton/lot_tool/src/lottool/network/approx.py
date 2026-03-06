from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from lottool.model.types import NetworkPlan


@dataclass
class NetApproxResult:
    loss_mw_tick: float
    loss_cost_rub: float
    risk_penalty_rub: float
    flags: List[str]


def approx_network_cost(
    plan: NetworkPlan, branch_flows: Dict[str, float], cfg_network: Dict[str, float]
) -> NetApproxResult:
    alpha = float(cfg_network.get("loss_alpha", 0.04))
    default_soft = float(cfg_network.get("soft_flow_mw", 30.0))
    wear_over = float(cfg_network.get("wear_overload_mw", 40.0))
    wear_pen = float(cfg_network.get("wear_risk_penalty_rub", 5.0))
    loss_tax = float(cfg_network.get("loss_tax", 2.0))

    loss = 0.0
    risk = 0.0
    flags: List[str] = []

    for b in plan.branches:
        flow = float(branch_flows.get(b.name, 0.0))
        soft = float(b.soft_flow_limit_mw or default_soft) or default_soft
        ratio = (flow / soft) if soft > 0 else 0.0
        loss += alpha * (ratio**2) * flow
        if flow > wear_over:
            risk += wear_pen * (flow - wear_over)
            flags.append(f"OVERLOAD_RISK:{b.name} flow={flow:.1f}MW>{wear_over:.1f}MW")

    return NetApproxResult(
        loss_mw_tick=loss, loss_cost_rub=loss * loss_tax, risk_penalty_rub=risk, flags=flags
    )


__all__ = ["NetApproxResult", "approx_network_cost"]
