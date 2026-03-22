from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Sequence

from ..models import GameSession, ObjectInstance, ObjectType
from .analysis_context import resolve_analysis_context


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _loss_map(session: GameSession) -> Dict[str, float]:
    cfg = dict((session.ruleset.config_json or {}).get("network", {}) or {})
    point_loss = dict(cfg.get("connection_loss_pct_by_point") or {})
    points = {
        str(point).strip().upper(): max(0.0, _to_float(loss, 0.0))
        for point, loss in point_loss.items()
        if str(point).strip()
    }
    if not points:
        points = {"A": 0.0}
    return points


def _default_point(session: GameSession) -> str:
    cfg = dict((session.ruleset.config_json or {}).get("network", {}) or {})
    fallback = str(cfg.get("default_connection_point") or "A").strip().upper()
    return fallback or "A"


def _resolve_point(
    *,
    default_point: str,
    district: Any = None,
    parameters: Mapping[str, Any] | None = None,
    allowed_points: Sequence[str] | None = None,
) -> str:
    params = dict(parameters or {})
    explicit = (
        params.get("connection_point")
        or params.get("point")
        or params.get("cell")
        or params.get("slot")
    )
    if explicit:
        out = str(explicit).strip().upper()
        return out or default_point

    out = str(default_point or "A").strip().upper()
    return out or default_point


def _usage_by_point(
    objects: Sequence[ObjectInstance],
    *,
    default_point: str,
    allowed_points: Sequence[str] | None = None,
) -> Dict[str, int]:
    usage: Dict[str, int] = {}
    for obj in objects:
        if not obj.is_active:
            continue
        point = _resolve_point(
            default_point=default_point,
            district=obj.district,
            parameters=obj.current_parameters_json,
            allowed_points=allowed_points,
        )
        usage[point] = usage.get(point, 0) + 1
    return usage


def _expected_power_from_type(
    *,
    object_type: ObjectType,
    parameters: Mapping[str, Any],
) -> float:
    category = _norm(object_type.category)
    params = dict(parameters or {})
    qty = max(1.0, _to_float(params.get("qty"), 1.0))
    if category == "consumer":
        return max(0.0, _to_float(params.get("expected_consumption_mw"), 1.0) * qty)
    if category == "generator":
        return max(0.0, _to_float(params.get("generation_mw"), 0.0) * qty)
    if category == "storage":
        return max(0.0, _to_float(params.get("capacity_mw_tick"), 0.0) * qty * 0.5)
    if category == "infrastructure":
        ports = max(1.0, _to_float(params.get("ports"), 1.0))
        soft_flow = max(0.0, _to_float(params.get("soft_flow_limit_mw"), 0.0))
        return max(ports * 0.25, soft_flow * 0.08) * qty
    return (
        max(
            max(0.0, _to_float(params.get("generation_mw"), 0.0)),
            max(0.0, _to_float(params.get("expected_consumption_mw"), 0.0)),
        )
        * qty
    )


def _expected_power_for_object(obj: ObjectInstance) -> float:
    object_type = obj.object_type
    if object_type is None:
        return 0.0
    parameters = dict(obj.merged_parameters() or {})
    return _expected_power_from_type(object_type=object_type, parameters=parameters)


def _usage_load_by_point(
    objects: Sequence[ObjectInstance],
    *,
    default_point: str,
    allowed_points: Sequence[str] | None = None,
) -> Dict[str, float]:
    usage: Dict[str, float] = {}
    for obj in objects:
        if not obj.is_active:
            continue
        point = _resolve_point(
            default_point=default_point,
            district=obj.district,
            parameters=obj.current_parameters_json,
            allowed_points=allowed_points,
        )
        usage[point] = usage.get(point, 0.0) + _expected_power_for_object(obj)
    return usage


def _children_count(objects: Sequence[ObjectInstance]) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for obj in objects:
        if not obj.is_active or not obj.parent_instance_id:
            continue
        counts[int(obj.parent_instance_id)] = counts.get(int(obj.parent_instance_id), 0) + 1
    return counts


def _infrastructure_support(
    objects: Sequence[ObjectInstance],
    *,
    default_point: str,
    allowed_points: Sequence[str] | None = None,
) -> Dict[str, Dict[str, float]]:
    child_counts = _children_count(objects)
    support: Dict[str, Dict[str, float]] = {}
    for obj in objects:
        if not obj.is_active or obj.object_type is None:
            continue
        if _norm(obj.object_type.category) != "infrastructure":
            continue
        params = dict(obj.merged_parameters() or {})
        point = _resolve_point(
            default_point=default_point,
            district=obj.district,
            parameters=params,
            allowed_points=allowed_points,
        )
        ports = max(0.0, _to_float(params.get("ports"), 0.0))
        soft_flow = max(0.0, _to_float(params.get("soft_flow_limit_mw"), 0.0))
        wear = max(0.0, _to_float(params.get("wear_impact"), 0.0))
        code = _norm(obj.object_type.code)
        if code in {"main_substation", "main", "main_substation_hq"}:
            capacity_bonus = soft_flow * 0.35 + ports * 2.0 - wear
        else:
            capacity_bonus = soft_flow * 0.20 + ports * 1.5 - wear
        slots_available = max(0.0, ports - float(child_counts.get(int(obj.id), 0)))
        bucket = support.setdefault(
            point,
            {
                "capacity_bonus_mw": 0.0,
                "slot_capacity": 0.0,
                "slot_remaining": 0.0,
                "nodes": 0.0,
            },
        )
        bucket["capacity_bonus_mw"] += max(0.0, capacity_bonus)
        bucket["slot_capacity"] += max(0.0, ports)
        bucket["slot_remaining"] += slots_available
        bucket["nodes"] += 1.0
    return support


def _global_slot_headroom(support_by_point: Mapping[str, Mapping[str, float]]) -> float:
    return float(
        sum(
            float((row or {}).get("slot_remaining", 0.0) or 0.0)
            for row in support_by_point.values()
        )
    )


def _category_mix_by_point(
    objects: Sequence[ObjectInstance],
    *,
    default_point: str,
    allowed_points: Sequence[str] | None = None,
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for obj in objects:
        if not obj.is_active or obj.object_type is None:
            continue
        point = _resolve_point(
            default_point=default_point,
            district=obj.district,
            parameters=obj.current_parameters_json,
            allowed_points=allowed_points,
        )
        bucket = out.setdefault(
            point,
            {"consumer": 0, "generator": 0, "storage": 0, "infrastructure": 0},
        )
        category = _norm(obj.object_type.category)
        if category in bucket:
            bucket[category] += 1
    return out


def _capacity_map(
    *,
    session: GameSession,
    points: Sequence[str],
    support_by_point: Mapping[str, Mapping[str, float]] | None = None,
) -> Dict[str, float]:
    network_cfg = dict((session.ruleset.config_json or {}).get("network", {}) or {})
    point_capacity_raw = dict(network_cfg.get("connection_capacity_mw_by_point") or {})
    fallback_capacity = max(0.0, _to_float(network_cfg.get("line_max_power_mw"), 0.0))
    out: Dict[str, float] = {}
    for point in points:
        raw = point_capacity_raw.get(point)
        capacity = _to_float(raw, fallback_capacity)
        if capacity <= 0.0:
            capacity = fallback_capacity
        capacity += max(
            0.0,
            _to_float((support_by_point or {}).get(point, {}).get("capacity_bonus_mw"), 0.0),
        )
        if capacity <= 0.0:
            capacity = float("inf")
        out[str(point)] = float(capacity)
    return out


def _consumer_profile_factor(object_type: ObjectType, forecast_summary: Mapping[str, Any]) -> float:
    averages = dict(forecast_summary.get("consumer_averages") or {})
    code = _norm(object_type.code)
    if code == "office":
        return max(0.4, _to_float(averages.get("office"), 1.0))
    if code == "factory":
        return max(0.4, _to_float(averages.get("factory"), 1.0))
    return max(0.4, _to_float(averages.get("housea"), _to_float(averages.get("house"), 1.0)))


def _generation_factor(object_type: ObjectType, forecast_summary: Mapping[str, Any]) -> float:
    code = _norm(object_type.code)
    avg_wind = _to_float(forecast_summary.get("avg_wind"), 0.0)
    avg_solar = _to_float(forecast_summary.get("avg_illumination"), 0.0)
    if code == "wind":
        return max(0.0, min(1.0, avg_wind * 0.12))
    if code in {"solar", "cyber_solar", "solarrobot"}:
        return max(0.0, min(1.0, avg_solar))
    return 1.0


def _baseline_value(
    *,
    object_type: ObjectType,
    parameters: Mapping[str, Any],
    forecast_summary: Mapping[str, Any],
) -> Dict[str, float]:
    market_price = max(0.1, _to_float(forecast_summary.get("avg_market_price"), 10.0))
    category = _norm(object_type.category)
    code = _norm(object_type.code)
    if category == "consumer":
        profile_factor = _consumer_profile_factor(object_type, forecast_summary)
        demand = max(
            0.0,
            _to_float(parameters.get("expected_consumption_mw"), 1.0) * profile_factor,
        )
        tariff = _to_float(parameters.get("tariff_rub_per_mw_tick"), 0.0)
        value = demand * (tariff - market_price)
        return {"value": value, "exposure": demand, "market_price": market_price}
    if category == "generator":
        generation = max(0.0, _to_float(parameters.get("generation_mw"), 0.0))
        efficiency = max(0.2, min(1.2, _to_float(parameters.get("efficiency"), 1.0)))
        factor = _generation_factor(object_type, forecast_summary)
        output = generation * efficiency * factor
        contracts = max(0.0, _to_float(parameters.get("contract_rub_per_tick"), 0.0))
        fuel_cost = 0.0
        if code == "tps":
            fuel_price = max(0.0, _to_float(parameters.get("fuel_price"), 0.5))
            eco_tax = max(0.0, _to_float(parameters.get("eco_tax_fuel"), 0.0))
            fuel_cost = output * (fuel_price / efficiency + eco_tax)
        eco_bonus = _to_float(parameters.get("eco_score"), 0.0) * 0.5
        value = output * market_price + eco_bonus - contracts - fuel_cost
        return {"value": value, "exposure": output, "market_price": market_price}
    if category == "storage":
        capacity = max(0.0, _to_float(parameters.get("capacity_mw_tick"), 0.0))
        contract = max(0.0, _to_float(parameters.get("contract_rub_per_tick"), 0.0))
        value = capacity * market_price * 0.08 - contract
        return {"value": value, "exposure": capacity * 0.3, "market_price": market_price}
    if category == "infrastructure":
        ports = max(1.0, _to_float(parameters.get("ports"), 1.0))
        wear = max(0.0, _to_float(parameters.get("wear_impact"), 0.0))
        soft_flow = max(0.0, _to_float(parameters.get("soft_flow_limit_mw"), 0.0))
        contract = max(0.0, _to_float(parameters.get("contract_rub_per_tick"), 0.0))
        value = ports * market_price * 0.5 + soft_flow * 0.12 - wear * market_price * 2.0 - contract
        return {"value": value, "exposure": ports + soft_flow * 0.05, "market_price": market_price}
    return {"value": 0.0, "exposure": 0.0, "market_price": market_price}


def _profile_key(object_type: ObjectType, parameters: Mapping[str, Any]) -> str:
    explicit = str(getattr(object_type, "forecast_profile_key", "") or "").strip()
    if explicit:
        return explicit
    fallback = str(parameters.get("profile") or "").strip()
    return fallback or "—"


def _portfolio_match_bonus(
    *,
    category: str,
    mix: Mapping[str, int],
    market_price: float,
) -> float:
    consumer_count = int(mix.get("consumer", 0) or 0)
    generator_count = int(mix.get("generator", 0) or 0)
    storage_count = int(mix.get("storage", 0) or 0)
    infrastructure_count = int(mix.get("infrastructure", 0) or 0)
    if category == "consumer":
        return float(generator_count * market_price * 0.25 + storage_count * market_price * 0.10)
    if category == "generator":
        return float(consumer_count * market_price * 0.22 + storage_count * market_price * 0.08)
    if category == "storage":
        return float((consumer_count + generator_count) * market_price * 0.10)
    if category == "infrastructure":
        return float((consumer_count + generator_count + storage_count) * market_price * 0.12)
    return infrastructure_count * market_price * 0.05


def _score_point_details(
    *,
    category: str,
    base_value: float,
    exposure: float,
    market_price: float,
    loss_pct: float,
    usage_count: int,
    used_power_mw: float,
    expected_power_mw: float,
    capacity_mw: float,
    slot_headroom: float,
    point_mix: Mapping[str, int],
) -> Dict[str, float | bool]:
    loss_share = max(0.0, loss_pct) / 100.0
    if category == "consumer":
        adjusted_value = base_value - exposure * market_price * loss_share
        loss_cost = exposure * market_price * loss_share
    else:
        adjusted_value = base_value * (1.0 - loss_share)
        loss_cost = max(0.0, base_value - adjusted_value)

    util_after = (
        (max(0.0, float(used_power_mw)) + max(0.0, float(expected_power_mw)))
        / max(1.0, float(capacity_mw))
        if math.isfinite(float(capacity_mw))
        else 0.0
    )
    congestion_penalty = (
        max(0.0, float(usage_count)) * market_price * 0.10
        + max(0.0, float(used_power_mw)) * market_price * 0.02
        + max(0.0, util_after) * max(0.0, float(expected_power_mw)) * market_price * 0.20
    )
    topology_penalty = (
        0.0 if slot_headroom > 0 or category == "infrastructure" else market_price * 1.5
    )
    headroom_ratio = (
        1.0
        if not math.isfinite(float(capacity_mw))
        else max(
            0.0,
            min(1.0, (float(capacity_mw) - float(used_power_mw)) / max(1.0, float(capacity_mw))),
        )
    )
    risk_penalty = max(0.0, util_after - 0.85) * market_price * max(1.0, expected_power_mw)
    portfolio_match_bonus = _portfolio_match_bonus(
        category=category,
        mix=point_mix,
        market_price=market_price,
    )
    score = (
        adjusted_value
        + portfolio_match_bonus
        - congestion_penalty
        - topology_penalty
        - risk_penalty
    )
    return {
        "score": float(score),
        "adjusted_value": float(adjusted_value),
        "loss_cost": float(loss_cost),
        "congestion_penalty": float(congestion_penalty),
        "topology_penalty": float(topology_penalty),
        "risk_penalty": float(risk_penalty),
        "portfolio_match_bonus": float(portfolio_match_bonus),
        "line_utilization": float(util_after),
        "headroom_ratio": float(headroom_ratio),
        "fits_topology": bool(slot_headroom > 0 or category == "infrastructure"),
    }


def recommend_connection_for_profile(
    *,
    session: GameSession,
    object_type: ObjectType,
    parameters: Mapping[str, Any] | None = None,
    district: Any = None,
    existing_objects: Sequence[ObjectInstance] | None = None,
    exclude_object_id: int | None = None,
) -> Dict[str, Any]:
    params = dict(parameters or {})
    merged_params = dict(object_type.default_parameters_json or {})
    merged_params.update(params)
    default_point = _default_point(session)
    point_loss = _loss_map(session)
    candidates = sorted(point_loss.keys())
    if not candidates:
        candidates = [default_point]
    allowed_points = sorted(set([*candidates, default_point]))

    objects = list(existing_objects or session.objects)
    if exclude_object_id is not None:
        objects = [obj for obj in objects if int(obj.id) != int(exclude_object_id)]
    usage = _usage_by_point(
        objects,
        default_point=default_point,
        allowed_points=allowed_points,
    )
    usage_load = _usage_load_by_point(
        objects,
        default_point=default_point,
        allowed_points=allowed_points,
    )
    support_by_point = _infrastructure_support(
        objects,
        default_point=default_point,
        allowed_points=allowed_points,
    )
    global_slot_headroom = _global_slot_headroom(support_by_point)
    mix_by_point = _category_mix_by_point(
        objects,
        default_point=default_point,
        allowed_points=allowed_points,
    )

    analysis_ctx = resolve_analysis_context(session)
    forecast_summary = dict(analysis_ctx.get("forecast_summary") or {})
    baseline = _baseline_value(
        object_type=object_type,
        parameters=merged_params,
        forecast_summary=forecast_summary,
    )
    category = _norm(object_type.category)
    expected_power_mw = _expected_power_from_type(object_type=object_type, parameters=merged_params)
    current_point = _resolve_point(
        default_point=default_point,
        district=district,
        parameters=merged_params,
        allowed_points=allowed_points,
    )
    if current_point not in point_loss:
        point_loss[current_point] = point_loss.get(default_point, 0.0)
        candidates = sorted(set([*candidates, current_point]))
    capacity_by_point = _capacity_map(
        session=session,
        points=candidates,
        support_by_point=support_by_point,
    )

    ranked: list[Dict[str, Any]] = []
    rejected_points: list[Dict[str, Any]] = []
    for point in candidates:
        capacity_mw = float(capacity_by_point.get(point, float("inf")))
        used_power_mw = float(usage_load.get(point, 0.0))
        remaining_capacity_mw = (
            float("inf")
            if not math.isfinite(capacity_mw)
            else max(0.0, float(capacity_mw - used_power_mw))
        )
        fits_capacity = bool(
            (not math.isfinite(capacity_mw))
            or (remaining_capacity_mw + 1e-9 >= max(0.0, float(expected_power_mw)))
        )
        point_support = dict(support_by_point.get(point) or {})
        slot_headroom = float(point_support.get("slot_remaining", 0.0) or 0.0)
        point_mix = dict(mix_by_point.get(point) or {})
        score_details = _score_point_details(
            category=category,
            base_value=baseline["value"],
            exposure=baseline["exposure"],
            market_price=baseline["market_price"],
            loss_pct=point_loss.get(point, 0.0),
            usage_count=usage.get(point, 0),
            used_power_mw=used_power_mw,
            expected_power_mw=expected_power_mw,
            capacity_mw=capacity_mw,
            slot_headroom=(
                global_slot_headroom if category != "infrastructure" else slot_headroom + 1.0
            ),
            point_mix=point_mix,
        )
        fits_topology = bool(score_details["fits_topology"])
        if not fits_capacity or not fits_topology:
            reasons: list[str] = []
            if not fits_capacity:
                reasons.append(
                    "capacity_exceeded: "
                    f"занято {used_power_mw:.2f} МВт из {capacity_mw:.2f}, "
                    f"кандидат требует {expected_power_mw:.2f} МВт"
                )
            if not fits_topology:
                reasons.append(
                    "topology_slots_exhausted: "
                    "в сети не осталось свободных инфраструктурных портов для нового подключения"
                )
            rejected_points.append(
                {
                    "point": point,
                    "loss_pct": float(point_loss.get(point, 0.0)),
                    "usage_count": int(usage.get(point, 0)),
                    "used_power_mw": float(used_power_mw),
                    "capacity_mw": float(capacity_mw),
                    "remaining_capacity_mw": float(remaining_capacity_mw),
                    "slot_remaining": float(slot_headroom),
                    "reason": "; ".join(reasons),
                }
            )
            continue
        ranked.append(
            {
                "point": point,
                "loss_pct": float(point_loss.get(point, 0.0)),
                "usage_count": int(usage.get(point, 0)),
                "used_power_mw": float(used_power_mw),
                "capacity_mw": float(capacity_mw),
                "remaining_capacity_mw": float(remaining_capacity_mw),
                "slot_remaining": float(slot_headroom),
                "fits_capacity": True,
                "fits_topology": True,
                "score": float(score_details["score"]),
                "adjusted_value": float(score_details["adjusted_value"]),
                "loss_cost": float(score_details["loss_cost"]),
                "congestion_penalty": float(score_details["congestion_penalty"]),
                "topology_penalty": float(score_details["topology_penalty"]),
                "risk_penalty": float(score_details["risk_penalty"]),
                "portfolio_match_bonus": float(score_details["portfolio_match_bonus"]),
                "line_utilization": float(score_details["line_utilization"]),
                "headroom_ratio": float(score_details["headroom_ratio"]),
                "explanation": (
                    f"потери {float(point_loss.get(point, 0.0)):.1f}%, "
                    f"остаток лимита {remaining_capacity_mw:.2f} МВт, "
                    f"согласование с портфелем {float(score_details['portfolio_match_bonus']):+.2f}"
                ),
            }
        )
    ranked.sort(key=lambda row: row["score"], reverse=True)

    if not ranked:
        current_loss = float(point_loss.get(current_point, point_loss.get(default_point, 0.0)))
        current_capacity = float(capacity_by_point.get(current_point, float("inf")))
        current_used = float(usage_load.get(current_point, 0.0))
        current_remaining = (
            float("inf")
            if not math.isfinite(current_capacity)
            else max(0.0, float(current_capacity - current_used))
        )
        message = (
            "Подключение не рекомендовано: "
            "не найдено точки, которая одновременно проходит по лимитам, потерям и сетевой связности."
        )
        return {
            "current_point": current_point,
            "recommended_point": current_point,
            "current_loss_pct": current_loss,
            "recommended_loss_pct": current_loss,
            "current_capacity_mw": current_capacity,
            "current_used_mw": current_used,
            "current_remaining_capacity_mw": current_remaining,
            "recommended_capacity_mw": current_capacity,
            "recommended_used_mw": current_used,
            "recommended_remaining_capacity_mw": current_remaining,
            "expected_power_mw": float(expected_power_mw),
            "estimated_delta": 0.0,
            "current_score": float("-inf"),
            "recommended_score": float("-inf"),
            "is_efficient": False,
            "profile_key": _profile_key(object_type, merged_params),
            "forecast_model_type": str(getattr(object_type, "forecast_model_type", "") or ""),
            "resource_dependencies": list(
                getattr(object_type, "resource_dependencies_json", []) or []
            ),
            "economic_role": str(getattr(object_type, "economic_role", "") or ""),
            "message": message,
            "ranked_points": [],
            "feasible_alternatives": [],
            "rejected_points": rejected_points,
            "global_slot_headroom": float(global_slot_headroom),
        }

    best = ranked[0]
    current = next((row for row in ranked if row["point"] == current_point), None)
    if current is None:
        current = {
            "point": current_point,
            "loss_pct": float(point_loss.get(current_point, point_loss.get(default_point, 0.0))),
            "usage_count": int(usage.get(current_point, 0)),
            "used_power_mw": float(usage_load.get(current_point, 0.0)),
            "capacity_mw": float(capacity_by_point.get(current_point, float("inf"))),
            "remaining_capacity_mw": (
                float("inf")
                if not math.isfinite(float(capacity_by_point.get(current_point, float("inf"))))
                else max(
                    0.0,
                    float(capacity_by_point.get(current_point, float("inf")))
                    - float(usage_load.get(current_point, 0.0)),
                )
            ),
            "slot_remaining": 0.0,
            "fits_capacity": False,
            "fits_topology": False,
            "score": float("-inf"),
        }
    delta = (
        float(best["score"] - current["score"])
        if math.isfinite(float(current["score"]))
        else float(best["score"])
    )
    is_efficient = bool(abs(delta) <= 0.1 and current.get("fits_capacity", False))
    if float(best["score"]) <= 0.0:
        message = (
            "Подключение формально возможно, но экономически слабое: "
            "издержки по потерям и загрузке сети выше ожидаемой отдачи."
        )
        is_efficient = False
    elif not bool(current.get("fits_capacity", False)):
        message = (
            f"Текущая точка {current_point} не проходит по лимитам. "
            f"Лучше {best['point']}: потери {best['loss_pct']:.1f}%, "
            f"остаток лимита {best['remaining_capacity_mw']:.2f} МВт."
        )
    elif is_efficient:
        message = (
            f"Текущее подключение {current_point} уже близко к оптимальному: "
            f"потери {current['loss_pct']:.1f}%, ограничений по лимиту нет."
        )
    else:
        message = (
            f"Рекомендуем точку {best['point']} вместо {current_point}: "
            f"выигрыш {delta:.2f} за тик, потери {best['loss_pct']:.1f}%, "
            f"остаток лимита {best['remaining_capacity_mw']:.2f} МВт."
        )

    feasible_alternatives = [row for row in ranked if row["point"] != str(best["point"])][:3]

    return {
        "current_point": current_point,
        "recommended_point": str(best["point"]),
        "current_loss_pct": float(current["loss_pct"]),
        "recommended_loss_pct": float(best["loss_pct"]),
        "current_capacity_mw": float(current["capacity_mw"]),
        "current_used_mw": float(current["used_power_mw"]),
        "current_remaining_capacity_mw": float(current["remaining_capacity_mw"]),
        "recommended_capacity_mw": float(best["capacity_mw"]),
        "recommended_used_mw": float(best["used_power_mw"]),
        "recommended_remaining_capacity_mw": float(best["remaining_capacity_mw"]),
        "expected_power_mw": float(expected_power_mw),
        "estimated_delta": delta,
        "current_score": float(current["score"]),
        "recommended_score": float(best["score"]),
        "is_efficient": is_efficient,
        "profile_key": _profile_key(object_type, merged_params),
        "forecast_model_type": str(getattr(object_type, "forecast_model_type", "") or ""),
        "resource_dependencies": list(getattr(object_type, "resource_dependencies_json", []) or []),
        "economic_role": str(getattr(object_type, "economic_role", "") or ""),
        "message": message,
        "ranked_points": ranked,
        "feasible_alternatives": feasible_alternatives,
        "rejected_points": rejected_points,
        "global_slot_headroom": float(global_slot_headroom),
    }


def recommendations_for_session_objects(session: GameSession) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    objects = list(session.objects)
    for obj in objects:
        if obj.object_type is None:
            continue
        out[int(obj.id)] = recommend_connection_for_profile(
            session=session,
            object_type=obj.object_type,
            parameters=obj.current_parameters_json,
            district=obj.district,
            existing_objects=objects,
            exclude_object_id=obj.id,
        )
    return out
