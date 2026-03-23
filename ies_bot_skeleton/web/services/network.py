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
    if not objects:
        return [ValidationIssue("EMPTY_SYSTEM", "В сессии нет объектов.", "warning")]
    report = validate_network([_energy_object(row) for row in objects if row.is_active])
    if not report.issues:
        return [ValidationIssue("NETWORK_OK", "Проверка пройдена без предупреждений.", "hint")]
    return [
        ValidationIssue(code=issue.code, message=issue.message, severity=issue.severity)
        for issue in report.issues
    ]
