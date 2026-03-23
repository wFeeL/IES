from __future__ import annotations

import json
from typing import Any, Dict, List

from ...common.budgeting import budget_snapshot
from ...domain.ies2026 import ies2026_config, plan_network_candidates, simulate_system
from ...application.analysis import rank_session_lots
from ..models import GameSession
from .analysis_context import resolve_analysis_context
from .evaluation import _base_objects, _forecast_pack_for_session


def _inventory_block(session: GameSession) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "main_substation": [],
        "mini_substations": [],
        "generators": [],
        "storages": [],
        "consumers": [],
    }
    for obj in session.objects:
        if not obj.is_active or obj.object_type is None:
            continue
        row = {
            "id": int(obj.id),
            "code": str(obj.object_type.code or ""),
            "name": str(obj.custom_name or obj.object_type.name or f"Объект {obj.id}"),
            "district": str(obj.district or ""),
            "source_lot_id": int(obj.source_lot_id or 0) or None,
            "parameters": dict(obj.merged_parameters() or {}),
        }
        code = str(obj.object_type.code or "")
        category = str(obj.object_type.category or "")
        if code == "main_substation":
            buckets["main_substation"].append(row)
        elif code in {"mini_substation", "mini_substation_a", "mini_substation_b"}:
            buckets["mini_substations"].append(row)
        elif category == "generator":
            buckets["generators"].append(row)
        elif category == "storage":
            buckets["storages"].append(row)
        else:
            buckets["consumers"].append(row)
    return buckets


def _topology_preview(session: GameSession) -> List[Dict[str, Any]]:
    objects = _base_objects(session)
    mains = [obj for obj in objects if obj.code == "main_substation"]
    existing = mains[:1]
    candidates = [obj for obj in objects if obj.object_id not in {row.object_id for row in existing}]
    previews = plan_network_candidates(existing_objects=existing, candidate_objects=candidates)
    out: List[Dict[str, Any]] = []
    for index, (_objects, report) in enumerate(previews, start=1):
        summary = ((report.topology_candidates or [{}])[0] if report.topology_candidates else {})
        out.append(
            {
                "candidate_id": str(summary.get("candidate_id") or f"net-{index}"),
                "strategy": str(summary.get("strategy") or "balanced"),
                "edge_list": list(summary.get("edge_list") or []),
                "district_map": dict(summary.get("district_map") or {}),
                "validation_block": dict(summary.get("validation_block") or {}),
                "expected_losses": float(summary.get("expected_losses", 0.0) or 0.0),
                "mandatory_fixes": list(summary.get("mandatory_fixes") or []),
            }
        )
    return out


def _installation_priority(session: GameSession, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    lot_scope_by_id = {int(lot.id): str(lot.scope or "normal") for lot in session.lots}
    priority_order = list((config.get("network") or {}).get("installation_priority") or [])
    rows: List[Dict[str, Any]] = []
    for obj in session.objects:
        if not obj.is_active or obj.object_type is None:
            continue
        code = str(obj.object_type.code or "")
        if code not in {"solar", "wind"}:
            continue
        scope = lot_scope_by_id.get(int(obj.source_lot_id or 0), "local")
        priority_key = f"{scope}_{code}"
        rows.append(
            {
                "object_id": int(obj.id),
                "code": code,
                "name": str(obj.custom_name or obj.object_type.name or f"Объект {obj.id}"),
                "priority_key": priority_key,
                "priority_rank": priority_order.index(priority_key)
                if priority_key in priority_order
                else len(priority_order) + 1,
                "placement_notes": "Порядок влияет на tie-break при проектировании и экспорте шаблона.",
                "blocked_by_higher_priority": False,
            }
        )
    rows.sort(key=lambda item: (int(item["priority_rank"]), int(item["object_id"])))
    for index, row in enumerate(rows):
        row["blocked_by_higher_priority"] = bool(index > 0 and row["priority_rank"] > rows[0]["priority_rank"])
    return rows


def _best_available_rows(session: GameSession) -> List[Dict[str, Any]]:
    ranked = rank_session_lots(
        session=session,
        lots=[lot for lot in session.lots if str(lot.status or "") == "available"],
        persist=False,
    )
    return ranked[:3]


def build_post_auction_plan(session: GameSession) -> Dict[str, Any]:
    analysis_ctx = resolve_analysis_context(session)
    forecast = analysis_ctx.get("forecast")
    forecast_pack = _forecast_pack_for_session(session, forecast)
    config = ies2026_config(dict(getattr(getattr(session, "ruleset", None), "config_json", {}) or {}))
    simulation = simulate_system(
        objects=_base_objects(session),
        forecast_pack=forecast_pack,
        ruleset_config=config,
        scenario_label="base",
    )
    budget = budget_snapshot(session)
    inventory = _inventory_block(session)
    topology_candidates = _topology_preview(session)
    best_available = _best_available_rows(session)
    won_lots = [
        {
            "id": int(lot.id),
            "name": str(lot.name or f"Лот {lot.id}"),
            "scope": str(lot.scope or "normal"),
            "purchase_price": float(lot.purchase_price or 0.0),
        }
        for lot in session.lots
        if str(lot.status or "") == "bought"
    ]
    dropped_lots = [
        {
            "id": int(lot.id),
            "name": str(lot.name or f"Лот {lot.id}"),
            "scope": str(lot.scope or "normal"),
        }
        for lot in session.lots
        if str(lot.status or "") == "rejected"
    ]
    tick_rows = list(simulation.tick_rows or [])
    return {
        "game": {
            "ruleset": str(getattr(getattr(session, "ruleset", None), "code", "ies_2026")),
            "horizon_ticks": int((config.get("time") or {}).get("horizon_ticks", 48) or 48),
            "selected_strategy": str(getattr(session, "selected_strategy", "balanced") or "balanced"),
            "selected_forecast": dict(analysis_ctx.get("forecast_context") or {}),
            "assumptions_versions": {
                "engine": "ies2026_domain_v2",
                "market_model": str((config.get("market") or {}).get("market_model", "aggregate_exchange_with_gp_fallback")),
                "network_model": "tree_validator_with_candidate_shortlist",
            },
        },
        "auction_result": {
            "won_lots": won_lots,
            "dropped_lots": dropped_lots,
            "available_second_round_opportunities": [
                {
                    "lot_id": int(row.get("lot_id", 0) or 0),
                    "lot_name": str(row.get("lot_name") or row.get("lot", {}).get("name") or "Лот"),
                    "recommended_counter_bid": float(
                        ((row.get("decision_summary") or {}).get("recommended_counter_bid") or 0.0)
                    ),
                    "hard_limit": float(((row.get("decision_summary") or {}).get("hard_limit") or 0.0)),
                }
                for row in best_available
            ],
            "all_pay_spend_used": float(session.allpay_spent or 0.0),
            "remaining_strategic_budget": float(budget.get("remaining_budget", 0.0) or 0.0),
        },
        "inventory": inventory,
        "topology_candidates": topology_candidates,
        "installation_priority": _installation_priority(session, config),
        "object_models": {
            "solar": [row for row in inventory["generators"] if row["code"] == "solar"],
            "wind": [row for row in inventory["generators"] if row["code"] == "wind"],
            "consumer": list(inventory["consumers"]),
            "storage": list(inventory["storages"]),
        },
        "market_plan": {
            "declared_sale_per_tick": [
                {"tick": bid.tick, "declared_mw": bid.declared_mw}
                for bid in simulation.market_bids
            ],
            "anti_dumping_cap_per_tick": [
                {"tick": bid.tick, "cap_mw": bid.anti_dumping_cap_mw}
                for bid in simulation.market_bids
            ],
            "reserve_policy": "Storage keeps reserve floor and discharges under deficit, anti-dumping support or expensive future buy windows.",
            "balancing_reserve": round(float(simulation.totals.storage.balancing + simulation.totals.storage.reserve), 4),
            "pricing_policy": {
                "exchange_band": f"{(config.get('market') or {}).get('exchange_price_min', 2.0)}-{(config.get('market') or {}).get('exchange_price_max', 20.0)}",
                "gp_low_price_factor": float((config.get("market") or {}).get("low_price_sale_factor", 0.55) or 0.55),
            },
        },
        "tick_model": tick_rows,
        "final_decision": {
            "selected_topology": topology_candidates[0] if topology_candidates else None,
            "selected_market_policy": "aggregate_exchange_with_gp_fallback",
            "selected_storage_policy": "reserve_floor_plus_price_aware_dispatch",
            "critical_checks": [issue.message for issue in simulation.topology.issues if issue.severity == "critical"],
            "top_risks": [issue.message for issue in simulation.topology.issues if issue.severity == "warning"][:5],
            "first_fixes": [
                issue.message
                for issue in simulation.topology.issues
                if issue.severity in {"critical", "warning"}
            ][:5],
        },
    }


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in [":", "#", "{", "}", "[", "]"]):
        return json.dumps(text, ensure_ascii=False)
    return text


def _to_yaml(value: Any, *, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(_to_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                rendered = _to_yaml(item, indent=indent + 2)
                rendered_lines = rendered.splitlines()
                if rendered_lines:
                    lines.append(f"{pad}- {rendered_lines[0].strip()}")
                    lines.extend(" " * (indent + 2) + line for line in rendered_lines[1:])
                else:
                    lines.append(f"{pad}-")
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_yaml_scalar(value)}"


def render_post_auction_plan_yaml(session: GameSession) -> str:
    plan = build_post_auction_plan(session)
    return _to_yaml(plan) + "\n"
