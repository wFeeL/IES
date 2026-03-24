from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ...domain.ies2026 import EnergyObject, validate_network
from ..models import ObjectInstance

GEN_CATEGORIES = {"generator", "storage"}
LOAD_CATEGORIES = {"consumer"}
MAIN_CODES = {"main_substation", "main", "main_substation_hq"}
MINI_CODES = {"mini_substation", "mini_substation_a", "mini_substation_b", "mini"}
INFRA_PARENT_CODES = MAIN_CODES | MINI_CODES


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class ValidationSummary:
    issues: List[ValidationIssue]
    critical_errors: List[ValidationIssue]
    warnings: List[ValidationIssue]
    optimization_hints: List[ValidationIssue]
    topology_candidates: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues": [issue.to_dict() for issue in self.issues],
            "critical_errors": [issue.to_dict() for issue in self.critical_errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "optimization_hints": [issue.to_dict() for issue in self.optimization_hints],
            "topology_candidates": list(self.topology_candidates),
        }


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _canonical_code(code: str) -> str:
    normalized = _norm(code)
    if normalized in {"mini_substation_a", "mini_substation_b", "mini_substation", "mini"}:
        return "mini_substation"
    if normalized in {"house", "housea", "house_a"}:
        return "house_a"
    if normalized in {"houseb", "house_b"}:
        return "house_b"
    if normalized in {"cyber_solar", "solarrobot"}:
        return "solar"
    if normalized == "tps":
        return "wind"
    return normalized


def _connection_inputs(parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, raw in enumerate(list(parameters.get("connection_inputs") or []), start=1):
        if not isinstance(raw, dict):
            continue
        rows.append(
            {
                "key": str(raw.get("key") or f"in{idx}"),
                "label": str(raw.get("label") or f"Ввод {idx}"),
                "required": bool(raw.get("required", True)),
                "parent_instance_id": raw.get("parent_instance_id"),
                "connection_point": str(
                    raw.get("connection_point") or raw.get("point") or raw.get("slot") or "A"
                ).upper(),
                "load_share": float(raw.get("load_share", 1.0) or 1.0),
            }
        )
    return rows


def _energy_object(row: ObjectInstance) -> EnergyObject:
    object_type = row.object_type
    params = dict(row.merged_parameters() or {})
    return EnergyObject(
        object_id=f"obj-{int(row.id)}",
        object_type_id=int(row.object_type_id),
        code=_canonical_code(object_type.code if object_type else ""),
        name=row.custom_name or (object_type.name if object_type else f"Объект {row.id}"),
        category=str(object_type.category if object_type else "consumer"),
        district=str(row.district or params.get("district") or "default"),
        parameters=params,
        source_lot_id=int(row.source_lot_id) if row.source_lot_id else None,
        is_candidate=False,
        is_active=bool(row.is_active),
        parent_id=f"obj-{int(row.parent_instance_id)}" if row.parent_instance_id else None,
        terminals=[],
    )


def validate_session_network(objects: List[ObjectInstance]) -> List[ValidationIssue]:
    return network_validation_summary(objects).issues


def network_validation_summary(objects: List[ObjectInstance]) -> ValidationSummary:
    if not objects:
        empty = [ValidationIssue("EMPTY_SYSTEM", "В сессии нет объектов.", "warning")]
        return ValidationSummary(
            issues=empty,
            critical_errors=[],
            warnings=empty,
            optimization_hints=[],
            topology_candidates=[],
        )
    report = validate_network([_energy_object(row) for row in objects if row.is_active])
    issues = [
        ValidationIssue(
            code=issue.code,
            message=(
                "Обнаружен цикл в дереве сети."
                if issue.code == "NETWORK_CYCLE"
                else issue.message
            ),
            severity=issue.severity,
        )
        for issue in report.issues
    ]
    if not issues:
        issues = [ValidationIssue("NETWORK_OK", "Проверка пройдена без предупреждений.", "hint")]
    critical_errors = [issue for issue in issues if issue.severity == "critical"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    optimization_hints = [issue for issue in issues if issue.severity == "hint"]
    topology_candidates: List[Dict[str, Any]] = []
    for index, candidate in enumerate(list(report.topology_candidates or []), start=1):
        topology_candidates.append(
            {
                "candidate_id": str(candidate.get("candidate_id") or f"candidate-{index}"),
                "strategy": str(candidate.get("strategy") or "balanced"),
                "edge_list": list(candidate.get("edge_list") or []),
                "district_map": dict(candidate.get("district_map") or {}),
                "validation_block": dict(candidate.get("validation_block") or {}),
                "expected_losses": float(candidate.get("expected_losses", 0.0) or 0.0),
                "mandatory_fixes": list(candidate.get("mandatory_fixes") or []),
            }
        )
    return ValidationSummary(
        issues=issues,
        critical_errors=critical_errors,
        warnings=warnings,
        optimization_hints=optimization_hints,
        topology_candidates=topology_candidates,
    )
