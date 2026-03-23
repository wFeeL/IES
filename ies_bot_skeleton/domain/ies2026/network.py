from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Dict, Iterable, List, Tuple

from .types import ConnectionTerminal, EnergyObject, NetworkValidationReport, TopologyIssue

SUPPLY_CATEGORIES = {"generator", "storage"}
LOAD_CATEGORIES = {"consumer"}
MAIN_CODES = {"main_substation"}
MINI_CODES = {"mini_substation"}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _is_main(obj: EnergyObject) -> bool:
    return _norm(obj.code) in MAIN_CODES


def _is_infrastructure(obj: EnergyObject) -> bool:
    return str(obj.category or "") == "infrastructure"


def _is_candidate(obj: EnergyObject) -> bool:
    return bool(obj.is_candidate)


def _point_from_payload(payload: Dict[str, object], fallback: str = "A") -> str:
    point = str(
        payload.get("connection_point")
        or payload.get("point")
        or payload.get("slot")
        or fallback
    ).strip()
    return (point or fallback).upper()


def _terminals_from_parameters(obj: EnergyObject) -> List[ConnectionTerminal]:
    params = dict(obj.parameters or {})
    raw_inputs = list(params.get("connection_inputs") or [])
    prepared: List[ConnectionTerminal] = []
    for idx, item in enumerate(raw_inputs, start=1):
        if not isinstance(item, dict):
            continue
        prepared.append(
            ConnectionTerminal(
                key=str(item.get("key") or f"in{idx}"),
                label=str(item.get("label") or f"Ввод {idx}"),
                required=bool(item.get("required", True)),
                parent_id=str(item.get("parent_instance_id") or "").strip() or None,
                connection_point=_point_from_payload(item, "A"),
                load_share=float(item.get("load_share", 1.0) or 1.0),
            )
        )
    if prepared:
        connected = [terminal for terminal in prepared if terminal.parent_id]
        if _norm(obj.code) == "factory" and len(connected) >= 2:
            for terminal in prepared:
                terminal.load_share = 0.5
        return prepared

    parent_id = str(obj.parent_id or "").strip() or None
    connection_point = _point_from_payload(params, "A")
    code = _norm(obj.code)
    if code == "hospital":
        return [
            ConnectionTerminal("input_a", "Ввод A", True, parent_id, connection_point, 0.5),
            ConnectionTerminal(
                "input_b",
                "Ввод B",
                True,
                str(params.get("secondary_parent_instance_id") or "").strip() or None,
                _point_from_payload(
                    {"connection_point": params.get("secondary_connection_point") or "B"},
                    "B",
                ),
                0.5,
            ),
        ]
    if code == "factory":
        second_parent = str(params.get("secondary_parent_instance_id") or "").strip() or None
        if second_parent:
            return [
                ConnectionTerminal("input_a", "Ввод A", False, parent_id, connection_point, 0.5),
                ConnectionTerminal(
                    "input_b",
                    "Ввод B",
                    False,
                    second_parent,
                    _point_from_payload(
                        {"connection_point": params.get("secondary_connection_point") or "B"},
                        "B",
                    ),
                    0.5,
                ),
            ]
        return [ConnectionTerminal("input_a", "Ввод", False, parent_id, connection_point, 1.0)]
    if _is_infrastructure(obj):
        return []
    return [ConnectionTerminal("input", "Подключение", True, parent_id, connection_point, 1.0)]


def ensure_terminals(objects: Iterable[EnergyObject]) -> List[EnergyObject]:
    prepared = []
    for source in objects:
        obj = deepcopy(source)
        obj.terminals = list(obj.terminals or _terminals_from_parameters(obj))
        prepared.append(obj)
    return prepared


def _ports_for_object(obj: EnergyObject) -> int:
    params = dict(obj.parameters or {})
    return max(0, int(params.get("ports", 0) or 0))


def _district_for_child(parent: EnergyObject, child: EnergyObject) -> str:
    return str(child.district or parent.district or "default")


def _category_conflict(stats: Dict[str, int], category: str) -> bool:
    if category in LOAD_CATEGORIES:
        return stats.get("supply", 0) > 0
    if category in SUPPLY_CATEGORIES:
        return stats.get("load", 0) > 0
    return False


def _infra_depths(objects: Dict[str, EnergyObject]) -> Dict[str, int]:
    depths: Dict[str, int] = {}

    def depth_for(node_id: str) -> int:
        if node_id in depths:
            return depths[node_id]
        node = objects.get(node_id)
        if node is None or _is_main(node) or not node.parent_id:
            depths[node_id] = 0
            return 0
        depths[node_id] = 1 + depth_for(str(node.parent_id))
        return depths[node_id]

    for object_id in objects:
        depth_for(object_id)
    return depths


def _used_ports(objects: Iterable[EnergyObject]) -> Dict[str, int]:
    usage: Dict[str, int] = defaultdict(int)
    for obj in objects:
        if not obj.is_active:
            continue
        if _is_infrastructure(obj) and obj.parent_id:
            usage[str(obj.parent_id)] += 1
        for terminal in obj.terminals:
            if terminal.parent_id:
                usage[str(terminal.parent_id)] += 1
    return dict(usage)


def _district_side_stats(objects: Iterable[EnergyObject]) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"load": 0, "supply": 0})
    for obj in objects:
        if not obj.is_active:
            continue
        district = str(obj.district or "default")
        if obj.category in LOAD_CATEGORIES:
            stats[district]["load"] += 1
        elif obj.category in SUPPLY_CATEGORIES:
            stats[district]["supply"] += 1
    return dict(stats)


def _eligible_parents(
    *,
    child: EnergyObject,
    terminal_index: int,
    objects_by_id: Dict[str, EnergyObject],
    available_ports: Dict[str, int],
) -> List[Tuple[str, int]]:
    infra = [obj for obj in objects_by_id.values() if obj.is_active and _is_infrastructure(obj)]
    depths = _infra_depths({obj.object_id: obj for obj in infra})
    district_stats = _district_side_stats(objects_by_id.values())
    out: List[Tuple[str, int]] = []
    for parent in infra:
        if available_ports.get(parent.object_id, 0) <= 0:
            continue
        district = _district_for_child(parent, child)
        if _category_conflict(district_stats.get(district, {}), child.category):
            continue
        # Hospitals and factories are allowed to reuse the same substation, but only if ports remain.
        depth = int(depths.get(parent.object_id, 0))
        score = depth * 10 + terminal_index
        out.append((parent.object_id, score))
    out.sort(key=lambda item: item[1])
    return out


def plan_network(
    *,
    existing_objects: Iterable[EnergyObject],
    candidate_objects: Iterable[EnergyObject],
) -> Tuple[List[EnergyObject], NetworkValidationReport]:
    objects = ensure_terminals([*existing_objects, *candidate_objects])
    by_id = {obj.object_id: obj for obj in objects}
    infra_nodes = [obj for obj in objects if _is_infrastructure(obj) and obj.is_active]
    available_ports: Dict[str, int] = {}
    usage = _used_ports(objects)
    for infra in infra_nodes:
        available_ports[infra.object_id] = max(0, _ports_for_object(infra) - usage.get(infra.object_id, 0))

    for obj in objects:
        if not obj.is_active or not _is_candidate(obj):
            continue
        if _is_infrastructure(obj):
            if _is_main(obj):
                obj.parent_id = None
                continue
            if obj.parent_id and available_ports.get(str(obj.parent_id), 0) > 0:
                available_ports[str(obj.parent_id)] -= 1
                continue
            choices = _eligible_parents(
                child=obj,
                terminal_index=0,
                objects_by_id=by_id,
                available_ports=available_ports,
            )
            if choices:
                parent_id = choices[0][0]
                obj.parent_id = parent_id
                available_ports[parent_id] = max(0, available_ports.get(parent_id, 0) - 1)
            continue

        for idx, terminal in enumerate(obj.terminals):
            if terminal.parent_id and available_ports.get(str(terminal.parent_id), 0) > 0:
                available_ports[str(terminal.parent_id)] -= 1
                continue
            choices = _eligible_parents(
                child=obj,
                terminal_index=idx,
                objects_by_id=by_id,
                available_ports=available_ports,
            )
            if not choices:
                continue
            chosen_parent = choices[0][0]
            terminal.parent_id = chosen_parent
            available_ports[chosen_parent] = max(0, available_ports.get(chosen_parent, 0) - 1)

    report = validate_network(objects)
    report.recommended_connections = {
        obj.object_id: deepcopy(obj.terminals)
        for obj in objects
        if obj.is_active and _is_candidate(obj) and obj.terminals
    }
    report.available_ports_by_node = {
        object_id: max(0, int(value))
        for object_id, value in available_ports.items()
    }
    return objects, report


def validate_network(objects: Iterable[EnergyObject]) -> NetworkValidationReport:
    prepared = ensure_terminals(objects)
    report = NetworkValidationReport()
    active = [obj for obj in prepared if obj.is_active]
    by_id = {obj.object_id: obj for obj in active}
    infra = [obj for obj in active if _is_infrastructure(obj)]
    mains = [obj for obj in infra if _is_main(obj)]
    if not mains:
        report.issues.append(
            TopologyIssue("NO_MAIN_SUBSTATION", "В системе отсутствует главная подстанция.", "critical")
        )
        return report
    if len(mains) > 1:
        report.issues.append(
            TopologyIssue(
                "MULTIPLE_MAIN_SUBSTATIONS",
                "В системе найдено несколько главных подстанций. Для расчёта должна быть одна корневая точка.",
                "critical",
            )
        )

    root_id = mains[0].object_id
    children: Dict[str, List[str]] = defaultdict(list)
    for obj in infra:
        if obj.parent_id:
            children[str(obj.parent_id)].append(obj.object_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def _dfs(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        visited.add(node_id)
        for child_id in children.get(node_id, []):
            if _dfs(child_id):
                return True
        visiting.remove(node_id)
        return False

    if _dfs(root_id):
        report.issues.append(
            TopologyIssue("NETWORK_CYCLE", "Обнаружен цикл в сети.", "critical")
        )

    reachable: set[str] = set()

    def _mark(node_id: str) -> None:
        if node_id in reachable:
            return
        reachable.add(node_id)
        for child_id in children.get(node_id, []):
            _mark(child_id)

    _mark(root_id)
    for obj in infra:
        if obj.object_id not in reachable:
            report.issues.append(
                TopologyIssue(
                    "NO_PATH_TO_MAIN",
                    f"Объект «{obj.name}» не имеет пути до главной подстанции.",
                    "critical",
                    obj.object_id,
                )
            )

    usage = _used_ports(active)
    for obj in infra:
        ports = _ports_for_object(obj)
        if ports and usage.get(obj.object_id, 0) > ports:
            report.issues.append(
                TopologyIssue(
                    "PORTS_EXCEEDED",
                    f"У узла «{obj.name}» превышено число подключений: {usage.get(obj.object_id, 0)}/{ports}.",
                    "critical",
                    obj.object_id,
                )
            )

    district_stats = _district_side_stats(active)
    for district, stats in sorted(district_stats.items()):
        if stats.get("load", 0) > 0 and stats.get("supply", 0) > 0:
            report.issues.append(
                TopologyIssue(
                    "DISTRICT_MIXED_ROLES",
                    f"В одном энергорайоне смешаны генерация и потребители: «{district}».",
                    "critical",
                )
            )

    for obj in active:
        if _is_infrastructure(obj):
            report.usable_fraction_by_object[obj.object_id] = 1.0 if obj.object_id in reachable else 0.0
            continue

        connected_terminals = [terminal for terminal in obj.terminals if terminal.parent_id]
        valid_connected = [
            terminal
            for terminal in connected_terminals
            if str(terminal.parent_id) in reachable
        ]

        if _norm(obj.code) == "hospital":
            if len(valid_connected) < 2:
                report.issues.append(
                    TopologyIssue(
                        "HOSPITAL_REQUIRES_TWO_INPUTS",
                        "Больница должна быть подключена двумя входами.",
                        "critical",
                        obj.object_id,
                    )
                )
                report.usable_fraction_by_object[obj.object_id] = 0.0
            else:
                report.usable_fraction_by_object[obj.object_id] = 1.0
            continue

        if _norm(obj.code) == "factory":
            if not valid_connected:
                report.issues.append(
                    TopologyIssue(
                        "FACTORY_NOT_CONNECTED",
                        "Завод должен иметь хотя бы одну точку подключения.",
                        "critical",
                        obj.object_id,
                    )
                )
                report.usable_fraction_by_object[obj.object_id] = 0.0
            elif len(valid_connected) == 1:
                report.issues.append(
                    TopologyIssue(
                        "FACTORY_SINGLE_INPUT",
                        "У завода подключена только одна точка. Вторая точка повысит устойчивость и снизит потери.",
                        "warning",
                        obj.object_id,
                    )
                )
                report.usable_fraction_by_object[obj.object_id] = 1.0
            else:
                report.usable_fraction_by_object[obj.object_id] = 1.0
            continue

        required_terminals = [terminal for terminal in obj.terminals if terminal.required]
        if required_terminals and len(valid_connected) < len(required_terminals):
            report.issues.append(
                TopologyIssue(
                    "OBJECT_NOT_CONNECTED",
                    f"Объект «{obj.name}» не имеет корректного пути до главной подстанции.",
                    "critical",
                    obj.object_id,
                )
            )
            report.usable_fraction_by_object[obj.object_id] = 0.0
        elif not valid_connected:
            report.issues.append(
                TopologyIssue(
                    "OBJECT_NOT_CONNECTED",
                    f"Объект «{obj.name}» не подключён к энергосистеме.",
                    "critical",
                    obj.object_id,
                )
            )
            report.usable_fraction_by_object[obj.object_id] = 0.0
        else:
            report.usable_fraction_by_object[obj.object_id] = 1.0

        if obj.source_lot_id and report.usable_fraction_by_object.get(obj.object_id, 0.0) <= 0.0:
            report.issues.append(
                TopologyIssue(
                    "PURCHASED_OBJECT_USELESS",
                    "Этот лот формально куплен, но его текущая схема подключения делает его бесполезным.",
                    "critical",
                    obj.object_id,
                )
            )

    return report


def loss_fraction_for_object(
    *,
    obj: EnergyObject,
    objects: Iterable[EnergyObject],
    config: Dict[str, object],
) -> float:
    network_cfg = dict(config.get("network") or {})
    point_loss = {
        str(key).upper(): float(value) / 100.0 if float(value) > 1.0 else float(value)
        for key, value in dict(network_cfg.get("connection_loss_pct_by_point") or {}).items()
    }
    base_edge = float(network_cfg.get("base_edge_loss_pct", 1.5) or 1.5) / 100.0
    depth_step = float(network_cfg.get("depth_loss_pct", 1.1) or 1.1) / 100.0
    district_cross = float(network_cfg.get("district_cross_loss_pct", 0.5) or 0.5) / 100.0
    by_id = {item.object_id: item for item in objects if item.is_active}

    terminal_losses: List[float] = []
    terminals = list(obj.terminals or _terminals_from_parameters(obj))
    if not terminals:
        return 0.0
    for terminal in terminals:
        if not terminal.parent_id:
            terminal_losses.append(1.0)
            continue
        depth = 0
        cursor = by_id.get(str(terminal.parent_id))
        while cursor is not None and cursor.parent_id:
            depth += 1
            cursor = by_id.get(str(cursor.parent_id))
        loss = base_edge + depth * depth_step
        point = str(terminal.connection_point or "A").upper()
        loss += float(point_loss.get(point, 0.0))
        parent = by_id.get(str(terminal.parent_id))
        if parent is not None and str(parent.district or "") != str(obj.district or ""):
            loss += district_cross
        terminal_losses.append(min(0.45, max(0.0, loss)))
    return min(0.45, max(0.0, sum(terminal_losses) / max(1, len(terminal_losses))))
