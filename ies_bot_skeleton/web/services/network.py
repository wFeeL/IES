from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from ..models import ObjectInstance

GEN_CATEGORIES = {"generator", "storage"}
LOAD_CATEGORIES = {"consumer"}
MAIN_CODES = {"main", "main_substation", "main_substation_hq"}
MINI_CODES = {"minia", "mini_substation_a", "minib", "mini_substation_b", "mini"}
INFRA_PARENT_CODES = MAIN_CODES | MINI_CODES


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


def _norm(code: str) -> str:
    return "".join(ch.lower() for ch in (code or "") if ch.isalnum() or ch == "_")


def validate_session_network(objects: List[ObjectInstance]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    active = [obj for obj in objects if obj.is_active]
    if not active:
        return [ValidationIssue("EMPTY_SYSTEM", "В сессии нет объектов", "warning")]

    object_by_id: Dict[int, ObjectInstance] = {int(obj.id): obj for obj in active}

    for obj in active:
        if obj.object_type is not None:
            continue
        issues.append(
            ValidationIssue(
                "OBJECT_TYPE_MISSING",
                f"Объект #{int(obj.id)} не имеет типа и не может участвовать в расчёте сети",
                "error",
            )
        )
    if any(issue.code == "OBJECT_TYPE_MISSING" for issue in issues):
        return issues

    main_nodes = [obj for obj in active if _norm(obj.object_type.code) in MAIN_CODES]
    if not main_nodes:
        issues.append(
            ValidationIssue("NO_MAIN_SUBSTATION", "В системе нет главной подстанции", "error")
        )
        return issues

    root = main_nodes[0]

    children: Dict[int, List[int]] = {obj.id: [] for obj in active}
    for obj in active:
        object_code = _norm(obj.object_type.code)
        parent_id = int(obj.parent_instance_id) if obj.parent_instance_id else None
        if object_code in MAIN_CODES and parent_id is not None:
            issues.append(
                ValidationIssue(
                    "MAIN_WITH_PARENT",
                    f"Главная подстанция #{int(obj.id)} не должна иметь родителя",
                    "error",
                )
            )
        if parent_id is None:
            if object_code not in MAIN_CODES:
                issues.append(
                    ValidationIssue(
                        "DANGLING_OBJECT",
                        f"Объект #{int(obj.id)} не подключен: не задан родитель",
                        "error",
                    )
                )
            if obj.source_lot_id is not None and object_code not in MAIN_CODES:
                issues.append(
                    ValidationIssue(
                        "PURCHASED_OBJECT_PENDING_INTEGRATION",
                        f"Объект #{int(obj.id)} из купленного лота не встроен в энергосистему",
                        "warning",
                    )
                )
            continue

        parent = object_by_id.get(int(parent_id))
        if parent is None:
            issues.append(
                ValidationIssue(
                    "MISSING_PARENT_OBJECT",
                    f"Объект #{int(obj.id)} ссылается на отсутствующего родителя #{int(parent_id)}",
                    "error",
                )
            )
            continue
        if not bool(parent.is_active):
            issues.append(
                ValidationIssue(
                    "PARENT_INACTIVE",
                    f"Объект #{int(obj.id)} ссылается на неактивного родителя #{int(parent_id)}",
                    "error",
                )
            )
            continue
        parent_code = _norm(parent.object_type.code if parent.object_type else "")
        parent_category = _norm(parent.object_type.category if parent.object_type else "")
        if parent_code not in INFRA_PARENT_CODES and parent_category != "infrastructure":
            issues.append(
                ValidationIssue(
                    "INVALID_PARENT_TYPE",
                    f"Объект #{int(obj.id)} подключен к недопустимому родителю #{int(parent.id)}",
                    "error",
                )
            )
            continue
        if parent_id in children:
            children[parent_id].append(obj.id)

    visited: Set[int] = set()
    stack: Set[int] = set()

    def dfs(node_id: int) -> bool:
        if node_id in stack:
            return True
        if node_id in visited:
            return False
        visited.add(node_id)
        stack.add(node_id)
        for child in children.get(node_id, []):
            if dfs(child):
                return True
        stack.remove(node_id)
        return False

    if dfs(root.id):
        issues.append(ValidationIssue("NETWORK_CYCLE", "Обнаружен цикл в дереве сети", "error"))

    reachable: Set[int] = set()

    def mark(node_id: int) -> None:
        if node_id in reachable:
            return
        reachable.add(node_id)
        for child in children.get(node_id, []):
            mark(child)

    mark(root.id)
    for obj in active:
        if obj.id not in reachable:
            issues.append(
                ValidationIssue(
                    "UNREACHABLE_OBJECT",
                    f"Объект #{obj.id} не подключен к дереву сети",
                    "error",
                )
            )

    # Port checks for main and mini substations.
    for obj in active:
        code = _norm(obj.object_type.code)
        if code not in MAIN_CODES:
            continue
        params = obj.merged_parameters()
        port_limit = int(params.get("ports", params.get("max_ports", 3)) or 3)
        used = len(children.get(obj.id, []))
        if used > port_limit:
            issues.append(
                ValidationIssue(
                    "MAIN_PORTS_EXCEEDED",
                    f"Превышено число подключений главной подстанции #{obj.id}: {used}/{port_limit}",
                    "error",
                )
            )

    for obj in active:
        code = _norm(obj.object_type.code)
        if code not in MINI_CODES:
            continue
        params = obj.merged_parameters()
        port_limit = int(params.get("ports", params.get("max_ports", 3)) or 3)
        used = len(children.get(obj.id, []))
        if used > port_limit:
            issues.append(
                ValidationIssue(
                    "MINI_PORTS_EXCEEDED",
                    f"Превышено число подключений мини-подстанции #{obj.id}: {used}/{port_limit}",
                    "error",
                )
            )

    district_groups: Dict[str, Dict[str, int]] = {}
    for obj in active:
        district = str(obj.district or "default")
        group = district_groups.setdefault(district, {"gen": 0, "load": 0, "restricted": 0})
        category = _norm(obj.object_type.category)
        if category in GEN_CATEGORIES:
            group["gen"] += 1
        elif category in LOAD_CATEGORIES:
            group["load"] += 1

        rules = obj.object_type.rules_json or {}
        if bool(rules.get("forbid_mixed_gen_load")):
            group["restricted"] += 1

    for district, stats in district_groups.items():
        if stats["restricted"] > 0 and stats["gen"] > 0 and stats["load"] > 0:
            issues.append(
                ValidationIssue(
                    "DISTRICT_RULE_CONFLICT",
                    f"Нарушено правило энергорайона '{district}': смешение генерации и нагрузки",
                    "warning",
                )
            )

    return issues
