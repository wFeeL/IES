from __future__ import annotations

from typing import List

from lottool.model.types import DeltaBreakdown


def fmt_delta(tag: str, d: DeltaBreakdown, reasons: List[str]) -> str:
    lines = [f"{tag}: ΔScore {d.delta_total:+.1f}"]
    lines.append(f"  Δincome: {d.delta_income:+.1f}")
    lines.append(f"  Δcontracts: {-d.delta_contracts:+.1f}")
    lines.append(f"  Δfuel+tax: {-d.delta_fuel_and_taxes:+.1f}")
    lines.append(f"  Δmarket: {-d.delta_market_net:+.1f}")
    lines.append(f"  Δnet-loss: {-d.delta_network_losses_cost:+.1f}")
    lines.append(f"  Δrisk: {-d.delta_risk_penalty:+.1f}")
    lines.append(f"  Δeco_value: {d.delta_eco_value:+.1f} (Δeco_points {d.delta_eco_points:+.1f})")
    if reasons:
        lines.append("Top reasons:")
        lines.extend([f"  - {r}" for r in reasons])
    if d.flags:
        lines.append("Flags:")
        lines.extend([f"  - {f}" for f in d.flags])
    return "\n".join(lines)


__all__ = ["fmt_delta"]
