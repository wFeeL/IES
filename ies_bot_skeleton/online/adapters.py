from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Set

from .capabilities import Capabilities, discover_capabilities
from .compat import CompatProfile
from .logging_utils import log_event
from .utils import safe_getattr


@dataclass
class Result:
    ok: bool
    applied: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    method_used: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.ok and self.applied)


@dataclass
class DetectedMethods:
    line_off: Optional[str]
    robot: Optional[str]
    buy: Optional[str]
    sell: Optional[str]
    tps: Optional[str]
    storage_signed: Optional[str]
    storage_charge: Optional[str]
    storage_discharge: Optional[str]


class OrdersAdapter:
    """Safe wrapper over `psm.orders` with explicit Result and diagnostics."""

    def __init__(
        self,
        psm: Any,
        *,
        compat_profile: CompatProfile,
        capabilities: Optional[Capabilities] = None,
        strict: bool = False,
        dry_run: bool = False,
        logger: Any = None,
        storage_sign_convention: str = "auto",
    ) -> None:
        self.psm = psm
        self.orders = safe_getattr(psm, "orders", None)
        self.compat_profile = compat_profile
        self.capabilities = capabilities or discover_capabilities(psm, compat_profile, logger=logger)
        self.strict = bool(strict)
        self.dry_run = bool(dry_run)
        self.logger = logger
        self.storage_sign_convention = storage_sign_convention

        self.detected = DetectedMethods(
            line_off=self.capabilities.method_for("line_off"),
            robot=self.capabilities.method_for("robot"),
            buy=self.capabilities.method_for("buy"),
            sell=self.capabilities.method_for("sell"),
            tps=self.capabilities.method_for("tps"),
            storage_signed=self.capabilities.method_for("storage"),
            storage_charge=self.capabilities.method_for("storage_charge"),
            storage_discharge=self.capabilities.method_for("storage_discharge"),
        )

        missing_critical = self._missing_critical_ops()
        if missing_critical:
            message = f"critical operations unavailable: {', '.join(sorted(missing_critical))}"
            log_event(
                self.logger,
                "warning",
                "IPS_CAPABILITIES_INCOMPLETE",
                strict=self.strict,
                missing=sorted(missing_critical),
                profile=self.compat_profile.season,
            )
            if self.strict:
                raise RuntimeError(message)

    def _missing_critical_ops(self) -> Set[str]:
        missing: Set[str] = set()
        storage_optional = bool(self.compat_profile.feature_flags.get("storage_optional", True))
        for op in self.compat_profile.required_operations:
            if op == "storage" and storage_optional:
                continue
            if not self.capabilities.is_supported(op):
                missing.add(op)
        return missing

    def _result_fail(
        self,
        operation: str,
        error_code: str,
        error_message: str,
        method_name: Optional[str],
        details: Optional[Dict[str, Any]] = None,
    ) -> Result:
        payload = dict(details or {})
        payload["strict"] = self.strict
        payload["dry_run"] = self.dry_run
        event = "IPS_METHOD_MISSING" if error_code == "IPS_METHOD_MISSING" else (
            "SIGNATURE_MISMATCH" if error_code == "SIGNATURE_MISMATCH" else "IPS_CALL_FAILED"
        )
        level = "error" if self.strict else "warning"
        log_event(
            self.logger,
            level,
            event,
            operation=operation,
            method=method_name,
            error_code=error_code,
            error_message=error_message,
            **payload,
        )
        if self.strict and operation in self.compat_profile.required_operations:
            raise RuntimeError(f"{operation} failed ({error_code}): {error_message}")
        return Result(
            ok=False,
            applied=False,
            error_code=error_code,
            error_message=error_message,
            method_used=method_name,
            details=payload,
        )

    def _invoke(
        self,
        operation: str,
        method_name: Optional[str],
        args: Sequence[Any],
        details: Optional[Dict[str, Any]] = None,
    ) -> Result:
        if not method_name:
            return self._result_fail(
                operation=operation,
                error_code="IPS_METHOD_MISSING",
                error_message=f"method for operation '{operation}' not found",
                method_name=None,
                details=details,
            )

        fn = safe_getattr(self.orders, method_name, None)
        if not callable(fn):
            return self._result_fail(
                operation=operation,
                error_code="IPS_METHOD_MISSING",
                error_message=f"orders.{method_name} is not callable",
                method_name=method_name,
                details=details,
            )

        if self.dry_run:
            log_event(
                self.logger,
                "info",
                "IPS_DRY_RUN_SKIPPED",
                operation=operation,
                method=method_name,
                args=list(args),
            )
            return Result(
                ok=True,
                applied=False,
                error_code=None,
                error_message=None,
                method_used=method_name,
                details={"dry_run": True, **(details or {})},
            )

        try:
            fn(*args)
            return Result(
                ok=True,
                applied=True,
                error_code=None,
                error_message=None,
                method_used=method_name,
                details=details or {},
            )
        except TypeError as exc:
            return self._result_fail(
                operation=operation,
                error_code="SIGNATURE_MISMATCH",
                error_message=str(exc),
                method_name=method_name,
                details={"args": list(args), **(details or {})},
            )
        except Exception as exc:  # noqa: BLE001 - ips SDK may raise arbitrary exceptions
            return self._result_fail(
                operation=operation,
                error_code="IPS_CALL_FAILED",
                error_message=str(exc),
                method_name=method_name,
                details={"args": list(args), **(details or {})},
            )

    def line_off(self, sub_id: str, line_no: int) -> Result:
        return self._invoke("line_off", self.detected.line_off, (str(sub_id), int(line_no)))

    def robot(self, solar_id: str, angle: int) -> Result:
        return self._invoke("robot", self.detected.robot, (str(solar_id), int(angle)))

    def buy(self, power: float, price: float) -> Result:
        method = self.detected.buy
        argc = self.capabilities.arg_count("buy")
        args = (float(power),) if argc == 1 else (float(power), float(price))
        return self._invoke("buy", method, args, details={"requested_price": float(price)})

    def sell(self, power: float, price: float) -> Result:
        method = self.detected.sell
        argc = self.capabilities.arg_count("sell")
        args = (float(power),) if argc == 1 else (float(power), float(price))
        return self._invoke("sell", method, args, details={"requested_price": float(price)})

    def tps_fuel(self, tps_id: str, fuel: float) -> Result:
        fuel = max(0.0, float(fuel))
        return self._invoke("tps", self.detected.tps, (str(tps_id), fuel))

    def set_storage_power(self, storage_id: str, power_mw: float) -> Result:
        power_mw = float(power_mw)
        if abs(power_mw) < 1e-9:
            return Result(
                ok=True,
                applied=False,
                error_code=None,
                error_message=None,
                method_used=None,
                details={"reason": "zero_power"},
            )

        if self.detected.storage_charge and self.detected.storage_discharge:
            if power_mw > 0:
                return self._invoke(
                    "storage_discharge",
                    self.detected.storage_discharge,
                    (str(storage_id), abs(power_mw)),
                )
            return self._invoke(
                "storage_charge",
                self.detected.storage_charge,
                (str(storage_id), abs(power_mw)),
            )

        method = self.detected.storage_signed
        if not method:
            return self._result_fail(
                operation="storage",
                error_code="IPS_METHOD_MISSING",
                error_message="storage command is not available",
                method_name=None,
                details={"storage_id": str(storage_id), "power_mw": power_mw},
            )

        conv = self.storage_sign_convention
        if conv == "auto":
            conv = self.compat_profile.storage_sign_convention
        if conv == "auto":
            conv = "pos_charge" if "charge" in method.lower() else "pos_discharge"

        signed = power_mw if conv == "pos_discharge" else -power_mw
        return self._invoke(
            "storage",
            method,
            (str(storage_id), float(signed)),
            details={"storage_id": str(storage_id), "power_mw": power_mw, "signed_power": signed, "sign_mode": conv},
        )
