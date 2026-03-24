from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ...domain.ies2026 import EnergyObject, validate_network
from ..models import ObjectInstance


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
    district_rows: List[Dict[str, Any]]
    assembly_plan: List[str]
    inventory_rows: List[Dict[str, Any]]

    @property
    def action_required(self) -> bool:
        return bool(self.critical_errors)

    @property
    def message(self) -> str:
        if self.critical_errors:
            return self.critical_errors[0].message
        if self.warnings:
            return self.warnings[0].message
        return "Энергосистема выглядит корректно."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues": [issue.to_dict() for issue in self.issues],
            "critical_errors": [issue.to_dict() for issue in self.critical_errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "optimization_hints": [issue.to_dict() for issue in self.optimization_hints],
            "topology_candidates": list(self.topology_candidates),
            "district_rows": list(self.district_rows),
            "assembly_plan": list(self.assembly_plan),
            "inventory_rows": list(self.inventory_rows),
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


def _district_rows(objects: List[ObjectInstance], topology_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    district_map: Dict[str, Dict[str, Any]] = {}
    candidate_districts = {}
    if topology_candidates:
        candidate_districts = dict(topology_candidates[0].get("district_map") or {})
    for obj in objects:
        if not obj.is_active:
            continue
        object_type = obj.object_type
        category = str(getattr(object_type, "category", "") or "")
        district = str(obj.district or "default")
        row = district_map.setdefault(
            district,
            {
                "district_id": district,
                "district_type": str(candidate_districts.get(district) or "unknown"),
                "objects": [],
                "upstream_node": None,
                "status": "ok",
            },
        )
        label = obj.custom_name or (object_type.name if object_type else f"Объект {obj.id}")
        row["objects"].append({
            "id": int(obj.id),
            "label": label,
            "code": str(getattr(object_type, "code", "") or ""),
            "category": category,
            "parent_id": int(obj.parent_instance_id) if obj.parent_instance_id else None,
        })
        if obj.parent_instance_id and row["upstream_node"] is None:
            row["upstream_node"] = int(obj.parent_instance_id)
    out: List[Dict[str, Any]] = []
    for district, row in sorted(district_map.items(), key=lambda item: item[0]):
        categories = {obj["category"] for obj in row["objects"] if obj["category"]}
        if "consumer" in categories and ("generator" in categories or "storage" in categories):
            row["district_type"] = "invalid_mixed"
            row["status"] = "blocking"
        elif "consumer" in categories:
            row["district_type"] = "load"
        elif "generator" in categories or "storage" in categories:
            row["district_type"] = "generation"
        elif "infrastructure" in categories:
            row["district_type"] = row["district_type"] if row["district_type"] != "unknown" else "infrastructure"
        row["object_labels"] = [obj["label"] for obj in row["objects"]]
        out.append(row)
    return out


def _inventory_rows(objects: List[ObjectInstance]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for obj in objects:
        object_type = obj.object_type
        params = dict(obj.current_parameters_json or {})
        integration_state = params.get("integration_state") or (
            "pending_connection" if obj.source_lot_id and not obj.parent_instance_id and obj.is_active else "integrated"
        )
        rows.append(
            {
                "id": int(obj.id),
                "label": obj.custom_name or (object_type.name if object_type else f"Объект {obj.id}"),
                "code": str(getattr(object_type, "code", "") or ""),
                "district": str(obj.district or "default"),
                "status": str(integration_state),
                "parent_id": int(obj.parent_instance_id) if obj.parent_instance_id else None,
                "is_active": bool(obj.is_active),
            }
        )
    return rows


def _assembly_plan(objects: List[ObjectInstance], district_rows: List[Dict[str, Any]], issues: List[ValidationIssue]) -> List[str]:
    plan: List[str] = []
    if not any(_canonical_code(getattr(obj.object_type, "code", "")) == "main_substation" for obj in objects if obj.is_active):
        plan.append("Установите главную подстанцию: без неё ни один объект не должен считаться корректно смонтированным.")
    else:
        plan.append("Зафиксируйте главную подстанцию как единственный корневой узел дерева сети.")
    if any(_canonical_code(getattr(obj.object_type, "code", "")) == "mini_substation" and obj.is_active for obj in objects):
        plan.append("Подключите все купленные миниподстанции к допустимым upstream-узлам и не оставляйте их неустановленными.")
    for row in district_rows:
        district_type = row.get("district_type")
        labels = ", ".join(row.get("object_labels") or []) or row.get("district_id")
        if district_type == "generation":
            plan.append(f"Сформируйте генераторную ветку {row['district_id']}: {labels}.")
        elif district_type == "load":
            plan.append(f"Сформируйте нагрузочную ветку {row['district_id']}: {labels}.")
        elif district_type == "invalid_mixed":
            plan.append(f"Разделите энергорайон {row['district_id']}: сейчас в нём смешаны производители и потребители ({labels}).")
    if any("больница" in issue.message.lower() for issue in issues):
        plan.append("Проверьте больницы: каждая должна иметь два независимых ввода.")
    if any("завод" in issue.message.lower() for issue in issues):
        plan.append("Проверьте заводы: второй ввод необязателен, но при его наличии нагрузка должна делиться между вводами.")
    plan.append("После раскладки проверьте отсутствие циклов, островов и путь каждого объекта до главной подстанции.")
    deduped: List[str] = []
    for step in plan:
        if step not in deduped:
            deduped.append(step)
    return deduped


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
            district_rows=[],
            assembly_plan=["Сначала добавьте объекты и начните с главной подстанции."],
            inventory_rows=[],
        )
    active_objects = [row for row in objects if row.is_active]
    report = validate_network([_energy_object(row) for row in active_objects])
    issues = [
        ValidationIssue(
            code=issue.code,
            message=("Обнаружен цикл в дереве сети." if issue.code == "NETWORK_CYCLE" else issue.message),
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
    district_rows = _district_rows(active_objects, topology_candidates)
    inventory_rows = _inventory_rows(objects)
    assembly_plan = _assembly_plan(active_objects, district_rows, issues)
    return ValidationSummary(
        issues=issues,
        critical_errors=critical_errors,
        warnings=warnings,
        optimization_hints=optimization_hints,
        topology_candidates=topology_candidates,
        district_rows=district_rows,
        assembly_plan=assembly_plan,
        inventory_rows=inventory_rows,
    )
