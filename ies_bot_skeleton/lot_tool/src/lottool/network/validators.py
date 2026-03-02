from __future__ import annotations

from typing import List, Tuple

from lottool.model.types import NetworkPlan


def validate_network_plan(plan: NetworkPlan) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    if plan.mode != "branches":
        issues.append(f"Unsupported network_plan.mode={plan.mode} (MVP supports 'branches').")
        return False, issues
    seen = set()
    for b in plan.branches:
        if b.name in seen:
            issues.append(f"Duplicate branch name: {b.name}")
        seen.add(b.name)
        if b.role not in ("gen", "load"):
            issues.append(f"Branch {b.name}: role must be 'gen' or 'load'")
        if b.soft_flow_limit_mw <= 0:
            issues.append(f"Branch {b.name}: soft_flow_limit_mw must be > 0")
    return len(issues) == 0, issues


__all__ = ["validate_network_plan"]
