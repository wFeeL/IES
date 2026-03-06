from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .compat import CompatProfile
from .logging_utils import log_event
from .utils import safe_getattr


@dataclass
class OperationCapability:
    operation: str
    supported: bool
    method_name: Optional[str] = None
    signature: Optional[str] = None
    arg_count: Optional[int] = None
    error: Optional[str] = None


@dataclass
class Capabilities:
    season: str
    operations: Dict[str, OperationCapability] = field(default_factory=dict)
    summary_details: Dict[str, Any] = field(default_factory=dict)

    def is_supported(self, operation: str) -> bool:
        op = self.operations.get(operation)
        return bool(op and op.supported)

    def method_for(self, operation: str) -> Optional[str]:
        op = self.operations.get(operation)
        return op.method_name if op else None

    def arg_count(self, operation: str) -> Optional[int]:
        op = self.operations.get(operation)
        return op.arg_count if op else None

    def summary(self) -> Dict[str, Any]:
        supported = sorted([name for name, cap in self.operations.items() if cap.supported])
        missing = sorted([name for name, cap in self.operations.items() if not cap.supported])
        methods = {
            name: cap.method_name for name, cap in self.operations.items() if cap.method_name
        }
        return {
            "season": self.season,
            "supported": supported,
            "missing": missing,
            "methods": methods,
            "details": dict(self.summary_details),
        }


def _arg_count(fn: Any) -> Optional[int]:
    try:
        sig = inspect.signature(fn)
    except Exception:
        return None
    count = 0
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            count += 1
    return count


def _sig_str(fn: Any) -> str:
    try:
        return str(inspect.signature(fn))
    except Exception:
        return "<unknown>"


def _detect_method(orders: Any, candidates: List[str]) -> OperationCapability:
    for name in candidates:
        fn = safe_getattr(orders, name, None)
        if callable(fn):
            return OperationCapability(
                operation="",
                supported=True,
                method_name=name,
                signature=_sig_str(fn),
                arg_count=_arg_count(fn),
            )
    return OperationCapability(operation="", supported=False, error="method not found")


def discover_capabilities(psm: Any, profile: CompatProfile, logger: Any = None) -> Capabilities:
    orders = safe_getattr(psm, "orders", None)
    ops: Dict[str, OperationCapability] = {}

    if orders is None:
        for op in (
            "line_off",
            "robot",
            "buy",
            "sell",
            "tps",
            "storage",
            "storage_charge",
            "storage_discharge",
        ):
            ops[op] = OperationCapability(operation=op, supported=False, error="orders is missing")
        caps = Capabilities(
            season=profile.season, operations=ops, summary_details={"orders_missing": True}
        )
        log_event(
            logger,
            "error",
            "IPS_METHOD_MISSING",
            operation="orders",
            error_message="psm.orders is missing",
        )
        return caps

    for op in (
        "line_off",
        "robot",
        "buy",
        "sell",
        "tps",
        "storage",
        "storage_charge",
        "storage_discharge",
    ):
        detected = _detect_method(orders, list(profile.method_candidates.get(op, [])))
        detected.operation = op
        ops[op] = detected

    if ops["storage_charge"].supported and ops["storage_discharge"].supported:
        # Prefer split API when it is fully available.
        ops["storage"].supported = False
        ops["storage"].method_name = None

    caps = Capabilities(
        season=profile.season,
        operations=ops,
        summary_details={
            "required_operations": list(profile.required_operations),
            "storage_optional": bool(profile.feature_flags.get("storage_optional", True)),
        },
    )

    missing_required: List[str] = []
    for op in profile.required_operations:
        if op == "storage" and profile.feature_flags.get("storage_optional", True):
            continue
        if not caps.is_supported(op):
            missing_required.append(op)

    log_event(
        logger,
        "info",
        "CAPABILITIES_SUMMARY",
        summary=caps.summary(),
        missing_required=missing_required,
    )
    return caps


__all__ = ["Capabilities", "OperationCapability", "discover_capabilities"]
