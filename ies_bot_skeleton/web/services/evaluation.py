from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast

from ..extensions import db
from ..models import (
    EvaluationResult,
    Forecast,
    ForecastPeriod,
    GameSession,
    Lot,
    LotItem,
    ObjectInstance,
)
from .analysis_context import resolve_analysis_context
from .connection_advisor import recommend_connection_for_profile
from .forecast_service import load_bundled_forecast_pack
from .network import validate_session_network
from .ruleset import strategy_weights
from .ui_text import strategy_label

DEFAULT_WEIGHTED = {"base": 0.50, "worst": 0.35, "best": 0.15}


class ForecastCompatibilityError(ValueError):
    def __init__(self, report: Dict[str, Any]) -> None:
        reasons = report.get("blocking_reasons") or []
        message = "; ".join(reasons) if reasons else "Прогноз несовместим с объектами сессии."
        super().__init__(message)
        self.report = dict(report)


@dataclass
class Asset:
    role: str
    code: str
    quantity: int
    parameters: Dict[str, Any]


@dataclass
class ScenarioSnapshot:
    income_total: float
    generation_income: float
    cost_total: float
    contracts: float
    fuel_and_taxes: float
    market_net: float
    penalties_total: float
    losses_total: float
    risk_penalty: float
    flexibility_value: float
    reserve_value: float
    eco_value: float
    net_profit: float
    utility_score: float
    served_load_revenue: float = 0.0
    avoided_market_purchase_value: float = 0.0
    export_revenue: float = 0.0
    market_purchase_cost: float = 0.0
    storage_operating_cost: float = 0.0
    overload_penalties: float = 0.0
    deficit_penalties: float = 0.0
    role_breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass
class DeltaSnapshot:
    delta_total: float
    delta_income: float
    delta_penalties: float
    delta_contracts: float
    delta_fuel_and_taxes: float
    delta_market_net: float
    delta_network_losses_cost: float
    delta_eco_value: float
    delta_risk_penalty: float
    delta_eco_points: float
    delta_served_load_revenue: float = 0.0
    delta_avoided_market_purchase_value: float = 0.0
    delta_export_revenue: float = 0.0
    delta_market_purchase_cost: float = 0.0
    delta_storage_operating_cost: float = 0.0
    delta_overload_penalties: float = 0.0
    delta_deficit_penalties: float = 0.0
    flags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    role_breakdown: Dict[str, float] = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip())


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _weighted_expected(config: Dict[str, Any], base: float, worst: float, best: float) -> float:
    weighted = ((config.get("evaluation", {}) or {}).get("weighted_expected", {}) or {}).copy()
    if not weighted:
        weighted = dict(DEFAULT_WEIGHTED)
    wb = float(weighted.get("base", DEFAULT_WEIGHTED["base"]))
    ww = float(weighted.get("worst", DEFAULT_WEIGHTED["worst"]))
    wbest = float(weighted.get("best", DEFAULT_WEIGHTED["best"]))
    return wb * base + ww * worst + wbest * best


def _session_lots(session: GameSession) -> Sequence[Lot]:
    return cast(Sequence[Lot], list(session.lots))


def _session_objects(session: GameSession) -> Sequence[ObjectInstance]:
    return cast(Sequence[ObjectInstance], list(session.objects))


def _lot_items(lot: Lot) -> Sequence[LotItem]:
    return cast(Sequence[LotItem], list(lot.items))


def _spent_total(session: GameSession) -> float:
    return float(
        sum(
            float(lot.purchase_price or 0.0)
            for lot in _session_lots(session)
            if str(lot.status or "") == "bought"
        )
    )


def _lot_reference_price(lot: Lot) -> float:
    if str(lot.status or "") == "bought" and lot.purchase_price is not None:
        return float(lot.purchase_price or 0.0)
    return float(lot.current_bid or 0.0)


def _reserved_lot_spend(lots: Sequence[Lot] | None) -> float:
    total = 0.0
    for lot in lots or []:
        if str(lot.status or "") == "bought":
            continue
        total += _lot_reference_price(lot)
    return float(total)


def _count_lot_objects(lots: Sequence[Lot] | None) -> int:
    total = 0
    for lot in lots or []:
        for item in _lot_items(lot):
            total += max(1, int(item.quantity or 1))
    return total


def _remaining_budget(session: GameSession, *, reserved_spend: float = 0.0) -> float:
    return max(
        0.0,
        float(session.budget_total or 0.0) - _spent_total(session) - max(0.0, float(reserved_spend)),
    )


def _portfolio_context(
    session: GameSession,
    *,
    reserved_spend: float = 0.0,
    extra_portfolio_lots: Sequence[Lot] | None = None,
) -> Dict[str, Any]:
    spent_total = _spent_total(session) + max(0.0, float(reserved_spend))
    return {
        "bought_lots_count": sum(
            1 for lot in _session_lots(session) if str(lot.status or "") == "bought"
        )
        + len(list(extra_portfolio_lots or [])),
        "spent_total": spent_total,
        "remaining_budget": max(0.0, float(session.budget_total or 0.0) - spent_total),
        "owned_objects_count": sum(1 for obj in _session_objects(session) if obj.is_active)
        + _count_lot_objects(extra_portfolio_lots),
    }


def _asset_role(category: str, economic_role: str) -> str:
    role = _norm(economic_role)
    if role in {"consumer", "generator", "storage", "infrastructure", "mixed"}:
        return role
    category_norm = _norm(category)
    if category_norm in {"consumer", "generator", "storage", "infrastructure"}:
        return category_norm
    return "mixed"


def _collect_lot_assets(lots: Sequence[Lot] | None = None) -> List[Asset]:
    assets: List[Asset] = []
    for lot in lots or []:
        for item in _lot_items(lot):
            if item.object_type is None:
                continue
            params = dict(item.object_type.default_parameters_json or {})
            params.update(dict(item.overrides_json or {}))
            params["forecast_profile_key"] = item.object_type.forecast_profile_key or params.get(
                "forecast_profile_key", ""
            )
            params["resource_dependencies"] = list(item.object_type.resource_dependencies_json or [])
            params.setdefault("qty", max(1, int(item.quantity or 1)))
            role = _asset_role(item.object_type.category, item.object_type.economic_role)
            assets.append(
                Asset(
                    role=role,
                    code=item.object_type.code,
                    quantity=max(1, int(item.quantity or 1)),
                    parameters=params,
                )
            )
    return assets


def _simulate_assets(
    *,
    assets: List[Asset],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    ticks: List[int],
    cfg: Dict[str, Any],
) -> Dict[str, ScenarioSnapshot]:
    return {
        "base": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="base"
        ),
        "worst": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="worst"
        ),
        "best": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="best"
        ),
    }


def _lot_role_profile(lots: Sequence[Lot]) -> Dict[str, Any]:
    role_counts = {
        "consumer": 0,
        "generator": 0,
        "storage": 0,
        "infrastructure": 0,
        "mixed": 0,
    }
    total_units = 0
    for asset in _collect_lot_assets(lots):
        qty = max(1, int(asset.quantity or 1))
        role = asset.role if asset.role in role_counts else "mixed"
        role_counts[role] += qty
        total_units += qty
    dominant_role = max(role_counts.items(), key=lambda row: row[1])[0] if total_units else "mixed"
    if sum(1 for value in role_counts.values() if value > 0) > 1:
        dominant_role = "mixed"
    role_multipliers = {
        "consumer": {"target": 0.92, "cautious": 1.05, "ceiling": 0.88},
        "generator": {"target": 1.08, "cautious": 1.00, "ceiling": 1.06},
        "storage": {"target": 0.98, "cautious": 1.10, "ceiling": 0.94},
        "infrastructure": {"target": 0.86, "cautious": 1.15, "ceiling": 0.80},
        "mixed": {"target": 1.00, "cautious": 1.00, "ceiling": 1.00},
    }
    return {
        "counts": role_counts,
        "dominant_role": dominant_role,
        "total_units": total_units,
        "multipliers": role_multipliers[dominant_role],
    }


def _lot_connection_outlook(
    *,
    session: GameSession,
    lots: Sequence[Lot],
) -> Dict[str, Any]:
    topology_issues = validate_session_network(list(_session_objects(session)))
    topology_errors = [issue for issue in topology_issues if str(issue.severity or "") == "error"]
    topology_invalid = bool(topology_errors)

    items: List[Dict[str, Any]] = []
    estimated_delta_total = 0.0
    blocked_items_count = 0
    feasible_items_count = 0
    weighted_loss_total = 0.0
    weight_total = 0.0

    for lot in lots:
        for item in _lot_items(lot):
            if item.object_type is None:
                continue
            qty = max(1, int(item.quantity or 1))
            params = dict(item.object_type.default_parameters_json or {})
            params.update(dict(item.overrides_json or {}))
            params.setdefault("qty", qty)
            rec = recommend_connection_for_profile(
                session=session,
                object_type=item.object_type,
                parameters=params,
                district=params.get("district"),
                existing_objects=list(session.objects),
            )
            estimated_delta = float(rec.get("estimated_delta", 0.0) or 0.0) * qty
            ranked_points = list(rec.get("ranked_points") or [])
            recommended_loss_pct = float(rec.get("recommended_loss_pct", 0.0) or 0.0)
            feasible = bool(ranked_points) and math.isfinite(float(rec.get("recommended_score", 0.0)))
            if feasible:
                feasible_items_count += qty
            else:
                blocked_items_count += qty
            estimated_delta_total += estimated_delta
            weighted_loss_total += recommended_loss_pct * qty
            weight_total += qty
            items.append(
                {
                    "lot_id": int(lot.id),
                    "lot_name": lot.name,
                    "object_type_code": item.object_type.code,
                    "object_type_name": item.object_type.name,
                    "category": item.object_type.category,
                    "quantity": qty,
                    "current_point": rec.get("current_point"),
                    "recommended_point": rec.get("recommended_point"),
                    "recommended_loss_pct": recommended_loss_pct,
                    "remaining_capacity_mw": float(
                        rec.get("recommended_remaining_capacity_mw", 0.0) or 0.0
                    ),
                    "estimated_delta": float(estimated_delta),
                    "profile_key": rec.get("profile_key") or item.object_type.forecast_profile_key or "",
                    "resource_dependencies": list(rec.get("resource_dependencies") or []),
                    "forecast_model_type": rec.get("forecast_model_type") or "",
                    "message": rec.get("message") or "",
                    "is_feasible": feasible,
                }
            )

    avg_loss_pct = float(weighted_loss_total / weight_total) if weight_total > 0 else 0.0
    system_fit_score = float(
        estimated_delta_total
        - blocked_items_count * 6.0
        - max(0.0, avg_loss_pct - 8.0) * max(1.0, weight_total) * 0.35
    )
    if topology_invalid:
        blocked_items_count = max(blocked_items_count, len(topology_errors))
        system_fit_score -= 50.0 + len(topology_errors) * 10.0
        message = (
            "Сетевая топология сессии некорректна: "
            + "; ".join(str(issue.message) for issue in topology_errors[:3])
        )
        status = "blocked"
    elif blocked_items_count > 0:
        message = (
            "Часть объектов лота не проходит по сетевым лимитам или точкам подключения. "
            "Это снижает рабочую цену."
        )
        status = "blocked"
    elif estimated_delta_total > 0.25:
        message = (
            "Подключение усиливает лот: найдены точки с меньшими потерями и достаточным запасом "
            "по мощности."
        )
        status = "supported"
    elif estimated_delta_total < -0.25:
        message = (
            "Сетевой fit слабый: даже лучшая точка подключения даёт мало экономической отдачи."
        )
        status = "risky"
    else:
        message = "Подключение нейтрально: сетевые ограничения не критичны, но явного бонуса нет."
        status = "neutral"
    return {
        "status": status,
        "message": message,
        "items": items,
        "estimated_delta_total": float(estimated_delta_total),
        "blocked_items_count": int(blocked_items_count),
        "feasible_items_count": int(feasible_items_count),
        "avg_recommended_loss_pct": float(avg_loss_pct),
        "system_fit_score": float(system_fit_score),
        "topology_invalid": bool(topology_invalid),
        "topology_issues": [issue.to_dict() for issue in topology_issues],
    }


def _period_series(
    periods: Sequence[ForecastPeriod],
) -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    ticks: List[int] = []
    profile_alias = {
        "house": "house_load",
        "housea": "house_load",
        "houseb": "house_load",
        "load_housea": "house_load",
        "load_houseb": "house_load",
        "consumption_houseb": "house_load",
        "office": "office_load",
        "load_office": "office_load",
        "factory": "factory_load",
        "load_factory": "factory_load",
    }
    for period in periods:
        tick = int(period.tick)
        ticks.append(tick)
        factor_rows = dict(period.factors_json or {})
        profile_rows = dict(period.profiles_json or {})

        if not factor_rows:
            if period.wind is not None:
                factor_rows["wind_factor"] = float(period.wind)
            if period.illumination is not None:
                factor_rows["solar_factor"] = float(period.illumination)
            if period.market_price is not None:
                factor_rows["market_price_buy"] = float(period.market_price)

        if not profile_rows:
            for raw_key, raw_value in (period.consumption_json or {}).items():
                key = profile_alias.get(_norm(raw_key))
                if key is None:
                    continue
                profile_rows[key] = _as_float(raw_value, 0.0)

        for key, value in factor_rows.items():
            factors.setdefault(_norm(key), {})[tick] = _as_float(value, 0.0)
        for key, value in profile_rows.items():
            profiles.setdefault(_norm(key), {})[tick] = _as_float(value, 0.0)
    return factors, profiles, sorted(set(ticks))


def _bundled_series() -> Tuple[Dict[str, Dict[int, float]], Dict[str, Dict[int, float]], List[int]]:
    pack = load_bundled_forecast_pack()
    factors: Dict[str, Dict[int, float]] = {}
    profiles: Dict[str, Dict[int, float]] = {}
    wind = (pack.get("wind", {}) or {}).get("wind", {}) or {}
    solar = (pack.get("solar", {}) or {}).get("solar", {}) or {}
    market = (pack.get("market", {}) or {}).get("price", {}) or {}
    if wind:
        factors["wind_factor"] = {int(k): _as_float(v, 0.0) for k, v in wind.items()}
    if solar:
        factors["solar_factor"] = {int(k): _as_float(v, 0.0) for k, v in solar.items()}
    if market:
        factors["market_price_buy"] = {int(k): _as_float(v, 0.0) for k, v in market.items()}

    load = pack.get("load", {}) or {}
    for raw_key, values in load.items():
        key = _norm(raw_key)
        if key in {"house", "housea", "houseb", "load_housea", "load_houseb"}:
            canonical = "house_load"
        elif key in {"office", "load_office"}:
            canonical = "office_load"
        elif key in {"factory", "load_factory"}:
            canonical = "factory_load"
        else:
            continue
        target = profiles.setdefault(canonical, {})
        for tick, value in (values or {}).items():
            target[int(tick)] = _as_float(value, 0.0)

    ticks = sorted(
        {
            *{int(tick) for rows in factors.values() for tick in rows.keys()},
            *{int(tick) for rows in profiles.values() for tick in rows.keys()},
        }
    )
    return factors, profiles, ticks


def _scenario_multipliers(cfg: Dict[str, Any], scenario: str) -> Dict[str, float]:
    scen_cfg = dict(cfg.get("scenarios", {}) or {})
    item = dict(scen_cfg.get(scenario, {}) or {})
    if item:
        return {
            "wind": _as_float(item.get("wind"), 1.0),
            "solar": _as_float(item.get("solar"), 1.0),
            "load": _as_float(item.get("load"), 1.0),
        }
    if scenario == "worst":
        return {"wind": 0.9, "solar": 0.8, "load": 1.1}
    if scenario == "best":
        return {"wind": 1.1, "solar": 1.2, "load": 0.9}
    return {"wind": 1.0, "solar": 1.0, "load": 1.0}


def _profile_value(
    profile_key: str, profiles: Dict[str, Dict[int, float]], tick: int, default: float = 1.0
) -> float:
    series = profiles.get(_norm(profile_key)) or {}
    if tick in series:
        return _as_float(series[tick], default)
    return float(default)


def _factor_value(
    factor_key: str, factors: Dict[str, Dict[int, float]], tick: int, default: float = 0.0
) -> float:
    series = factors.get(_norm(factor_key)) or {}
    if tick in series:
        return _as_float(series[tick], default)
    return float(default)


def _consumer_demand_mw(
    *,
    expected_consumption_mw: float,
    profile_value: float,
    load_scale: float,
) -> float:
    # Canonical forecast rows may come either as factors (0..1.5) or absolute MW (>1.5).
    if profile_value <= 1.5:
        demand = max(0.0, expected_consumption_mw) * max(0.0, profile_value)
    else:
        demand = max(0.0, profile_value)
    return max(0.0, demand * max(0.0, load_scale))


def _wind_generation_mw(
    *,
    wind_value: float,
    generation_mw: float,
    efficiency: float,
    object_defaults: Dict[str, Any],
) -> float:
    speed_or_factor = max(0.0, wind_value)
    cap = max(0.0, generation_mw)
    eff = _clamp(efficiency, 0.1, 1.2)
    if speed_or_factor <= 1.5:
        return cap * speed_or_factor * eff
    k = max(0.0, _as_float(object_defaults.get("wind_k_default"), 0.08))
    physical = k * (speed_or_factor**3)
    return min(cap, physical) * eff


def _solar_generation_mw(
    *,
    solar_value: float,
    generation_mw: float,
    efficiency: float,
) -> float:
    irradiation_or_factor = max(0.0, solar_value)
    cap = max(0.0, generation_mw)
    eff = _clamp(efficiency, 0.1, 1.2)
    if irradiation_or_factor <= 1.5:
        return cap * irradiation_or_factor * eff
    return min(cap, irradiation_or_factor) * eff


def _asset_profile_key(asset: Asset) -> str:
    explicit = _norm(asset.parameters.get("forecast_profile_key", ""))
    if explicit:
        return explicit
    fallback = _norm(asset.parameters.get("profile", ""))
    if fallback in {"house", "housea", "load_housea"}:
        return "house_load"
    if fallback in {"office", "load_office"}:
        return "office_load"
    if fallback in {"factory", "load_factory"}:
        return "factory_load"
    if fallback:
        return fallback
    if asset.role == "consumer":
        if _norm(asset.code) in {"factory"}:
            return "factory_load"
        if _norm(asset.code) in {"office"}:
            return "office_load"
        return "house_load"
    if asset.role == "generator" and _norm(asset.code) in {"wind"}:
        return "wind_profile"
    if asset.role == "generator" and _norm(asset.code) in {"solar", "cyber_solar", "solarrobot"}:
        return "solar_profile"
    if asset.role == "storage":
        return "storage_default_profile"
    return ""


def _collect_assets(session: GameSession, extra_lots: Sequence[Lot] | None = None) -> List[Asset]:
    assets: List[Asset] = []
    for obj in _session_objects(session):
        if not obj.is_active or obj.object_type is None:
            continue
        params = dict(obj.object_type.default_parameters_json or {})
        params.update(dict(obj.current_parameters_json or {}))
        params.setdefault("district", obj.district)
        params["forecast_profile_key"] = obj.object_type.forecast_profile_key or params.get(
            "forecast_profile_key", ""
        )
        params["resource_dependencies"] = list(obj.object_type.resource_dependencies_json or [])
        role = _asset_role(obj.object_type.category, obj.object_type.economic_role)
        assets.append(
            Asset(
                role=role,
                code=obj.object_type.code,
                quantity=max(1, int(params.get("qty", 1) or 1)),
                parameters=params,
            )
        )
    assets.extend(_collect_lot_assets(extra_lots))
    return assets


def _simulate_scenario(
    *,
    assets: List[Asset],
    factors: Dict[str, Dict[int, float]],
    profiles: Dict[str, Dict[int, float]],
    ticks: List[int],
    cfg: Dict[str, Any],
    scenario: str,
) -> ScenarioSnapshot:
    scales = _scenario_multipliers(cfg, scenario)
    market_cfg = dict(cfg.get("market", {}) or {})
    fine_cfg = dict(cfg.get("fine", {}) or {})
    net_cfg = dict(cfg.get("network", {}) or {})
    eco_cfg = dict(cfg.get("eco", {}) or {})
    storage_cfg = dict(cfg.get("storage", {}) or {})
    evaluation_cfg = dict(cfg.get("evaluation", {}) or {})
    object_defaults = dict(cfg.get("object_defaults", {}) or {})

    market_buy_default = _as_float(market_cfg.get("external_buy_price"), 10.0)
    market_sell_default = _as_float(market_cfg.get("external_sell_price"), 2.0)
    penalty_rate = _as_float(fine_cfg.get("class3_rub_per_mw"), 10.0)
    factory_penalty_rate = _as_float(fine_cfg.get("factory_rub_per_mw"), 10.0)
    loss_tax = _as_float(net_cfg.get("loss_tax"), 1.0)
    base_loss_rate = 0.05
    default_connection_point = str(net_cfg.get("default_connection_point") or "A").strip().upper()
    point_losses_raw = dict(net_cfg.get("connection_loss_pct_by_point") or {})
    point_loss_by_connection: Dict[str, float] = {
        str(key).strip().upper(): max(0.0, _as_float(value, 0.0)) / 100.0
        for key, value in point_losses_raw.items()
        if str(key).strip()
    }
    default_point_loss = float(point_loss_by_connection.get(default_connection_point, 0.0))
    storage_throughput_cost = max(
        0.0,
        _as_float(
            evaluation_cfg.get("storage_operating_cost_per_mwh_throughput"),
            _as_float(storage_cfg.get("operating_cost_per_mwh_throughput"), 0.0),
        ),
    )

    storage_capacity = max(0.0, _as_float(storage_cfg.get("capacity_mw_tick"), 20.0))
    storage_charge = max(0.0, _as_float(storage_cfg.get("charge_rate_mw"), 5.0))
    storage_discharge = max(0.0, _as_float(storage_cfg.get("discharge_rate_mw"), 5.0))
    storage_leak = _clamp(_as_float(storage_cfg.get("leak_fraction_per_tick"), 0.0), 0.0, 0.3)

    market_buy_cap_raw = market_cfg.get("market_max_power")
    if market_buy_cap_raw in (None, ""):
        market_buy_cap_raw = market_cfg.get("instant_buy_max_power")
    market_buy_capacity = (
        float("inf")
        if market_buy_cap_raw in (None, "", 0, 0.0)
        else max(0.0, _as_float(market_buy_cap_raw, 0.0))
    )

    market_sell_cap_raw = market_cfg.get("instant_sell_max_power")
    market_sell_capacity = (
        float("inf")
        if market_sell_cap_raw in (None, "", 0, 0.0)
        else max(0.0, _as_float(market_sell_cap_raw, 0.0))
    )

    point_capacity_raw = dict(net_cfg.get("connection_capacity_mw_by_point") or {})
    line_capacity_default = max(0.0, _as_float(net_cfg.get("line_max_power_mw"), 0.0))

    def asset_connection_point(asset: Asset) -> str:
        return str(
            asset.parameters.get("connection_point")
            or asset.parameters.get("district")
            or default_connection_point
        ).strip().upper() or default_connection_point

    def asset_connection_loss(asset: Asset) -> float:
        point = asset_connection_point(asset)
        return float(point_loss_by_connection.get(point, default_point_loss))

    infrastructure_support_by_point: Dict[str, Dict[str, float]] = {}
    infrastructure_capacity_bonus_total = 0.0
    infrastructure_loss_reduction_total = 0.0
    for asset in assets:
        if asset.role != "infrastructure":
            continue
        qty = max(1, int(asset.quantity or 1))
        point = asset_connection_point(asset)
        ports = max(0.0, _as_float(asset.parameters.get("ports"), 0.0)) * qty
        soft_flow = max(0.0, _as_float(asset.parameters.get("soft_flow_limit_mw"), 0.0)) * qty
        wear = max(0.0, _as_float(asset.parameters.get("wear_impact"), 0.0)) * qty
        capacity_bonus = max(0.0, soft_flow * 0.25 + ports * 1.5 - wear)
        loss_reduction = min(0.04, ports * 0.004 + soft_flow * 0.0005)
        bucket = infrastructure_support_by_point.setdefault(
            point,
            {"capacity_bonus_mw": 0.0, "loss_reduction": 0.0},
        )
        bucket["capacity_bonus_mw"] += capacity_bonus
        bucket["loss_reduction"] += loss_reduction
        infrastructure_capacity_bonus_total += capacity_bonus
        infrastructure_loss_reduction_total += loss_reduction

    point_capacity_by_connection: Dict[str, float] = {}
    for point in set(
        [
            *point_loss_by_connection.keys(),
            *point_capacity_raw.keys(),
            *(asset_connection_point(asset) for asset in assets),
            default_connection_point,
        ]
    ):
        base_capacity = max(0.0, _as_float(point_capacity_raw.get(point), line_capacity_default))
        support_bonus = max(
            0.0,
            _as_float(
                (infrastructure_support_by_point.get(point) or {}).get("capacity_bonus_mw"),
                0.0,
            ),
        )
        capacity = base_capacity + support_bonus
        if capacity <= 0.0:
            capacity = float("inf")
        point_capacity_by_connection[point] = float(capacity)

    all_connection_losses = [asset_connection_loss(asset) for asset in assets] or [default_point_loss]
    avg_connection_loss = float(sum(all_connection_losses) / len(all_connection_losses))
    network_loss_rate = max(
        0.0,
        base_loss_rate
        + avg_connection_loss * 0.5
        - min(0.03, infrastructure_loss_reduction_total * 0.5),
    )

    contracts = sum(
        _as_float(asset.parameters.get("contract_rub_per_tick"), 0.0) * asset.quantity
        for asset in assets
    ) * len(ticks)
    income_total = 0.0
    market_net = 0.0
    fuel_and_taxes = 0.0
    penalties_total = 0.0
    losses_total = 0.0
    eco_value = 0.0
    risk_penalty = 0.0
    served_load_revenue = 0.0
    avoided_market_purchase_value = 0.0
    export_revenue = 0.0
    market_purchase_cost = 0.0
    storage_operating_cost = 0.0
    overload_penalties = 0.0
    deficit_penalties = 0.0

    role_breakdown = {
        "consumer": 0.0,
        "generator": 0.0,
        "storage": 0.0,
        "infrastructure": 0.0,
        "mixed": 0.0,
    }
    storage_state = 0.0
    storage_max = 0.0
    for asset in assets:
        if asset.role != "storage":
            continue
        storage_max += (
            max(0.0, _as_float(asset.parameters.get("capacity_mw_tick"), storage_capacity))
            * asset.quantity
        )
    storage_state = 0.0

    for tick in ticks:
        wind_factor = _factor_value("wind_factor", factors, tick, 0.0) * scales["wind"]
        solar_factor = _factor_value("solar_factor", factors, tick, 0.0) * scales["solar"]
        market_buy = _factor_value("market_price_buy", factors, tick, market_buy_default)
        market_sell = _factor_value(
            "market_price_sell",
            factors,
            tick,
            market_sell_default if market_sell_default > 0 else market_buy * 0.5,
        )
        fuel_price = _factor_value(
            "fuel_price", factors, tick, _as_float(cfg.get("tps", {}).get("fuel_price"), 0.5)
        )

        demand_total = 0.0
        demand_factory = 0.0
        renewable_generation = 0.0
        thermal_units: List[Dict[str, Any]] = []
        point_usage_mw: Dict[str, float] = {}

        for asset in assets:
            qty = max(1, int(asset.quantity))
            profile_key = _asset_profile_key(asset)
            profile_value = _profile_value(profile_key, profiles, tick, 1.0)
            point = asset_connection_point(asset)
            if asset.role == "consumer":
                base_load = _as_float(asset.parameters.get("expected_consumption_mw"), 1.0)
                connection_loss = asset_connection_loss(asset)
                load = (
                    _consumer_demand_mw(
                        expected_consumption_mw=base_load,
                        profile_value=profile_value,
                        load_scale=scales["load"],
                    )
                    * qty
                    * (1.0 + connection_loss)
                )
                demand_total += load
                point_usage_mw[point] = point_usage_mw.get(point, 0.0) + load
                if _norm(asset.code) == "factory":
                    demand_factory += load
            elif asset.role == "generator":
                code = _norm(asset.code)
                eff = _clamp(_as_float(asset.parameters.get("efficiency"), 1.0), 0.1, 1.2)
                delivery_factor = max(0.0, 1.0 - asset_connection_loss(asset))
                generation_mw = (
                    max(0.0, _as_float(asset.parameters.get("generation_mw"), 0.0)) * qty
                )
                if code in {"wind"}:
                    wind_input = max(0.0, wind_factor) * max(0.0, profile_value)
                    supply = _wind_generation_mw(
                        wind_value=wind_input,
                        generation_mw=generation_mw,
                        efficiency=eff,
                        object_defaults=object_defaults,
                    )
                    supply *= delivery_factor
                    renewable_generation += supply
                    point_usage_mw[point] = point_usage_mw.get(point, 0.0) + supply
                    eco_value += (
                        supply
                        * _as_float(eco_cfg.get("wind_points_per_mw_tick"), 1.0)
                        * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
                    )
                elif code in {"solar", "cyber_solar", "solarrobot"}:
                    solar_input = max(0.0, solar_factor) * max(0.0, profile_value)
                    supply = _solar_generation_mw(
                        solar_value=solar_input,
                        generation_mw=generation_mw,
                        efficiency=eff,
                    )
                    supply *= delivery_factor
                    renewable_generation += supply
                    point_usage_mw[point] = point_usage_mw.get(point, 0.0) + supply
                    eco_value += (
                        supply
                        * _as_float(eco_cfg.get("solar_points_per_mw_tick"), 1.0)
                        * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
                    )
                else:
                    thermal_units.append(
                        {
                            "point": point,
                            "capacity": generation_mw * delivery_factor,
                            "eta": _clamp(
                                _as_float(
                                    asset.parameters.get("efficiency"),
                                    _as_float(cfg.get("tps", {}).get("eta_nominal"), 0.9),
                                ),
                                0.2,
                                1.0,
                            ),
                            "tax": _as_float(
                                asset.parameters.get("eco_tax_fuel"),
                                _as_float(cfg.get("tps", {}).get("eco_tax_fuel"), 0.0),
                            ),
                        }
                    )
            elif asset.role == "infrastructure":
                continue

        if storage_state > 0 and storage_leak > 0:
            storage_state = max(0.0, storage_state * (1.0 - storage_leak))

        losses_mw = max(0.0, renewable_generation) * network_loss_rate
        losses_total += losses_mw * loss_tax
        renewable_available = max(0.0, renewable_generation - losses_mw)
        renewable_to_load = min(demand_total, renewable_available)
        renewable_surplus = max(0.0, renewable_available - renewable_to_load)
        deficit = max(0.0, demand_total - renewable_to_load)

        if renewable_to_load > 0:
            avoided_market_purchase_value += renewable_to_load * market_buy
            role_breakdown["generator"] += renewable_to_load * market_buy

        storage_charge_total = 0.0
        storage_discharge_total = 0.0
        if deficit > 0 and storage_state > 0 and storage_max > 0:
            discharge_cap = storage_discharge * max(1.0, storage_max / max(storage_capacity, 1.0))
            discharge = min(deficit, storage_state, discharge_cap)
            storage_state -= discharge
            storage_discharge_total += discharge
            deficit = max(0.0, deficit - discharge)
            point_usage_mw[default_connection_point] = (
                point_usage_mw.get(default_connection_point, 0.0) + discharge
            )
            avoided_market_purchase_value += discharge * market_buy
            role_breakdown["storage"] += discharge * market_buy
            eco_value += (
                discharge
                * _as_float(eco_cfg.get("storage_discharge_points_per_mw_tick"), 0.0)
                * _as_float(eco_cfg.get("eco_point_value_rub"), 1.0)
            )

        for unit in thermal_units:
            if deficit <= 0:
                break
            variable_cost = fuel_price / max(0.2, unit["eta"]) + unit["tax"]
            if variable_cost > market_buy:
                continue
            dispatch = min(deficit, unit["capacity"])
            if dispatch <= 0:
                continue
            deficit -= dispatch
            fuel_and_taxes += dispatch * variable_cost
            point_usage_mw[str(unit.get("point") or default_connection_point)] = (
                point_usage_mw.get(str(unit.get("point") or default_connection_point), 0.0) + dispatch
            )
            avoided_market_purchase_value += dispatch * market_buy
            role_breakdown["generator"] += dispatch * market_buy - dispatch * variable_cost

        market_purchase = 0.0
        if deficit > 0:
            market_purchase = min(deficit, market_buy_capacity)
            market_purchase_cost += market_purchase * market_buy
            market_net += market_purchase * market_buy
            deficit = max(0.0, deficit - market_purchase)

        if deficit > 0:
            deficit_penalties += max(0.0, deficit - 1.0) * penalty_rate
            if demand_factory > 0 and demand_total > 0:
                deficit_penalties += (
                    demand_factory * (deficit / demand_total) * factory_penalty_rate
                )

        for point, used_mw in point_usage_mw.items():
            capacity = float(point_capacity_by_connection.get(point, float("inf")))
            if math.isfinite(capacity) and used_mw > capacity:
                overload = max(0.0, used_mw - capacity)
                overload_penalties += overload * market_buy * 0.60
                risk_penalty += overload * market_buy * 0.30
                role_breakdown["infrastructure"] += min(
                    overload * market_buy * 0.20,
                    infrastructure_capacity_bonus_total * market_buy * 0.05,
                )

        risk_penalty += (
            max(0.0, losses_mw * market_buy * 0.08)
            + max(0.0, deficit) * (penalty_rate + factory_penalty_rate) * 0.5
            + max(0.0, avg_connection_loss)
            * max(demand_total, renewable_generation)
            * market_buy
            * 0.03
        )
        infra_relief = min(
            risk_penalty,
            (infrastructure_capacity_bonus_total * 0.01 + infrastructure_loss_reduction_total * 10.0)
            * market_buy
            * 0.10,
        )
        risk_penalty = max(0.0, risk_penalty - infra_relief)
        role_breakdown["infrastructure"] += float(max(0.0, infra_relief))

        surplus = renewable_surplus
        if surplus > 0 and storage_max > 0 and storage_state < storage_max:
            charge_cap = storage_charge * max(1.0, storage_max / max(storage_capacity, 1.0))
            charge = min(surplus, storage_max - storage_state, charge_cap)
            storage_state += charge
            surplus -= charge
            storage_charge_total += charge
            point_usage_mw[default_connection_point] = (
                point_usage_mw.get(default_connection_point, 0.0) + charge
            )

        if surplus > 0:
            exported = min(surplus, market_sell_capacity)
            if exported > 0:
                export_revenue += exported * market_sell
                market_net -= exported * market_sell
                role_breakdown["generator"] += exported * market_sell

        served_ratio = 1.0
        if demand_total > 1e-9:
            served_ratio = _clamp((demand_total - deficit) / demand_total, 0.0, 1.0)
        for asset in assets:
            if asset.role != "consumer":
                continue
            qty = max(1, int(asset.quantity))
            base_load = _as_float(asset.parameters.get("expected_consumption_mw"), 1.0)
            profile_key = _asset_profile_key(asset)
            profile_value = _profile_value(profile_key, profiles, tick, 1.0)
            connection_loss = asset_connection_loss(asset)
            demand = (
                _consumer_demand_mw(
                    expected_consumption_mw=base_load,
                    profile_value=profile_value,
                    load_scale=scales["load"],
                )
                * qty
                * (1.0 + connection_loss)
            )
            served = demand * served_ratio
            tariff = _as_float(asset.parameters.get("tariff_rub_per_mw_tick"), 0.0)
            served_load_revenue += served * tariff
            role_breakdown["consumer"] += served * tariff

        if storage_charge_total > 0 or storage_discharge_total > 0:
            storage_throughput = storage_charge_total + storage_discharge_total
            storage_operating_cost += storage_throughput * storage_throughput_cost
            storage_cycle_value = (
                role_breakdown["storage"] - storage_throughput * storage_throughput_cost
            )
            role_breakdown["storage"] = max(0.0, storage_cycle_value)

    penalties_total = overload_penalties + deficit_penalties
    income_total = served_load_revenue + export_revenue + max(0.0, eco_value)
    cost_total = contracts + fuel_and_taxes + storage_operating_cost + max(0.0, market_net)
    net_profit = income_total - cost_total - penalties_total - losses_total
    utility_score = net_profit - risk_penalty

    return ScenarioSnapshot(
        income_total=float(income_total),
        generation_income=float(export_revenue),
        cost_total=float(cost_total),
        contracts=float(contracts),
        fuel_and_taxes=float(fuel_and_taxes),
        market_net=float(market_net),
        penalties_total=float(penalties_total),
        losses_total=float(losses_total),
        risk_penalty=float(risk_penalty),
        flexibility_value=float(max(0.0, role_breakdown.get("storage", 0.0))),
        reserve_value=float(max(0.0, role_breakdown.get("infrastructure", 0.0))),
        eco_value=float(eco_value),
        net_profit=float(net_profit),
        utility_score=float(utility_score),
        served_load_revenue=float(served_load_revenue),
        avoided_market_purchase_value=float(avoided_market_purchase_value),
        export_revenue=float(export_revenue),
        market_purchase_cost=float(market_purchase_cost),
        storage_operating_cost=float(storage_operating_cost),
        overload_penalties=float(overload_penalties),
        deficit_penalties=float(deficit_penalties),
        role_breakdown={key: float(value) for key, value in role_breakdown.items()},
    )


def _to_delta(before: ScenarioSnapshot, after: ScenarioSnapshot) -> DeltaSnapshot:
    role_delta: Dict[str, float] = {}
    for role in set(before.role_breakdown.keys()) | set(after.role_breakdown.keys()):
        role_delta[role] = float(
            after.role_breakdown.get(role, 0.0) - before.role_breakdown.get(role, 0.0)
        )
    return DeltaSnapshot(
        delta_total=float(after.utility_score - before.utility_score),
        delta_income=float(after.income_total - before.income_total),
        delta_penalties=float(after.penalties_total - before.penalties_total),
        delta_contracts=float(after.contracts - before.contracts),
        delta_fuel_and_taxes=float(after.fuel_and_taxes - before.fuel_and_taxes),
        delta_market_net=float(after.market_net - before.market_net),
        delta_network_losses_cost=float(after.losses_total - before.losses_total),
        delta_eco_value=float(after.eco_value - before.eco_value),
        delta_risk_penalty=float(after.risk_penalty - before.risk_penalty),
        delta_eco_points=float((after.eco_value - before.eco_value) / 2.0),
        delta_served_load_revenue=float(after.served_load_revenue - before.served_load_revenue),
        delta_avoided_market_purchase_value=float(
            after.avoided_market_purchase_value - before.avoided_market_purchase_value
        ),
        delta_export_revenue=float(after.export_revenue - before.export_revenue),
        delta_market_purchase_cost=float(after.market_purchase_cost - before.market_purchase_cost),
        delta_storage_operating_cost=float(
            after.storage_operating_cost - before.storage_operating_cost
        ),
        delta_overload_penalties=float(after.overload_penalties - before.overload_penalties),
        delta_deficit_penalties=float(after.deficit_penalties - before.deficit_penalties),
        role_breakdown=role_delta,
    )


def _human_reasons(delta_obj: DeltaSnapshot, top_k: int = 5) -> List[str]:
    contrib = [
        ("Доход", delta_obj.delta_income),
        ("Штрафы", -delta_obj.delta_penalties),
        ("Контракты", -delta_obj.delta_contracts),
        ("Топливо и налоги", -delta_obj.delta_fuel_and_taxes),
        ("Рынок", -delta_obj.delta_market_net),
        ("Потери", -delta_obj.delta_network_losses_cost),
        ("Риск", -delta_obj.delta_risk_penalty),
        ("Эко-эффект", delta_obj.delta_eco_value),
    ]
    contrib.sort(key=lambda row: abs(row[1]), reverse=True)
    out: List[str] = []
    for key, value in contrib:
        if abs(value) < 1e-6:
            continue
        out.append(f"{key}: {value:+.2f}")
        if len(out) >= top_k:
            break
    return out


def _scenario_comment(
    *,
    scenario: str,
    utility_total: float,
    net_profit: float,
    recommended_bid: float,
    penalties_total: float,
    losses_total: float,
) -> str:
    scenario_key = _norm(scenario)
    if recommended_bid <= 0.0:
        if scenario_key == "worst":
            return (
                "Worst: сценарий уязвим, безопасная ставка отсутствует; "
                "дефицит и потери перекрывают эффект лота."
            )
        if scenario_key == "best":
            return (
                "Best: потенциал высокий, но при текущих параметрах риск всё ещё выше "
                "допустимого для ставки."
            )
        return "Base: при текущих вводных сценарий не поддерживает безопасную ставку."
    if scenario_key == "worst":
        return (
            "Worst: повышенная чувствительность к потерям и штрафам; "
            f"убытки по рискам {losses_total:.2f}, штрафы {penalties_total:.2f}."
        )
    if scenario_key == "best":
        return (
            "Best: выраженная синергия генерации и спроса, "
            "сетевые издержки компенсируются ростом маржи."
        )
    if utility_total >= 0 and net_profit >= 0:
        return "Base: рабочий нейтральный сценарий, лот поддерживает устойчивую доходность."
    if utility_total >= 0:
        return "Base: полезность положительная, но прибыль чувствительна к цене входа."
    return "Base: сценарий слабый, требуется более консервативная цена покупки."


def _scenario_row(
    *,
    label: str,
    scenario: str = "base",
    delta_obj: Any,
    current_price: float,
    pwin: float,
    remaining_budget: float,
) -> Dict[str, Any]:
    del pwin, remaining_budget
    market_delta = float(getattr(delta_obj, "delta_market_net", 0.0) or 0.0)
    served_load_revenue = float(
        getattr(delta_obj, "delta_served_load_revenue", getattr(delta_obj, "delta_income", 0.0))
        or 0.0
    )
    export_revenue = float(
        getattr(delta_obj, "delta_export_revenue", max(0.0, -market_delta)) or 0.0
    )
    expenses_total = float(current_price) + max(
        0.0, float(getattr(delta_obj, "delta_contracts", 0.0) or 0.0)
    )
    expenses_total += max(0.0, float(getattr(delta_obj, "delta_fuel_and_taxes", 0.0) or 0.0))
    expenses_total += max(
        0.0,
        float(getattr(delta_obj, "delta_storage_operating_cost", 0.0) or 0.0),
    )
    expenses_total += max(
        0.0,
        float(getattr(delta_obj, "delta_market_purchase_cost", market_delta) or 0.0),
    )
    penalties_total = max(0.0, float(getattr(delta_obj, "delta_penalties", 0.0) or 0.0))
    losses_total = max(
        0.0,
        float(getattr(delta_obj, "delta_network_losses_cost", 0.0) or 0.0),
    ) + max(0.0, float(getattr(delta_obj, "delta_risk_penalty", 0.0) or 0.0))
    income_total = (
        max(0.0, served_load_revenue)
        + max(0.0, export_revenue)
        + max(0.0, float(getattr(delta_obj, "delta_eco_value", 0.0) or 0.0))
    )
    utility_total = float(delta_obj.delta_total)
    net_profit = income_total - expenses_total - penalties_total - losses_total
    reserve_margin = max(5.0, 0.10 * max(0.0, net_profit))
    recommended_bid = max(0.0, net_profit - reserve_margin)
    bid_ceiling = max(recommended_bid, net_profit)
    return {
        "label": label,
        "revenue_total": float(income_total),
        "cost_total": float(expenses_total),
        "penalties_total": float(penalties_total),
        "losses_total": float(losses_total),
        "net_profit": float(net_profit),
        "utility_score": float(utility_total),
        "bid_ceiling": float(bid_ceiling),
        "recommended_bid": float(recommended_bid),
        "explanation": _scenario_comment(
            scenario=scenario,
            utility_total=float(utility_total),
            net_profit=float(net_profit),
            recommended_bid=float(recommended_bid),
            penalties_total=float(penalties_total),
            losses_total=float(losses_total),
        ),
        # Backward-compatible aliases:
        "income_total": float(income_total),
        "expenses_total": float(expenses_total),
        "utility_total": float(utility_total),
        "expected_net_profit_at_current_price": float(net_profit),
        "comment": _scenario_comment(
            scenario=scenario,
            utility_total=float(utility_total),
            net_profit=float(net_profit),
            recommended_bid=float(recommended_bid),
            penalties_total=float(penalties_total),
            losses_total=float(losses_total),
        ),
    }


def _financial_breakdown(
    *,
    base_delta: Any,
    current_price: float,
    hard_bid: float,
    model_risk_premium: float = 0.0,
) -> Dict[str, Any]:
    market_delta = float(getattr(base_delta, "delta_market_net", 0.0) or 0.0)
    served_load_revenue = float(
        getattr(base_delta, "delta_served_load_revenue", getattr(base_delta, "delta_income", 0.0))
        or 0.0
    )
    export_revenue = float(
        getattr(base_delta, "delta_export_revenue", max(0.0, -market_delta)) or 0.0
    )
    avoided_market_purchase_value = float(
        getattr(base_delta, "delta_avoided_market_purchase_value", 0.0) or 0.0
    )
    storage_operating_cost = float(getattr(base_delta, "delta_storage_operating_cost", 0.0) or 0.0)
    overload_penalties = float(getattr(base_delta, "delta_overload_penalties", 0.0) or 0.0)
    deficit_penalties = float(getattr(base_delta, "delta_deficit_penalties", 0.0) or 0.0)
    income = {
        "object_income": float(max(0.0, served_load_revenue)),
        "market_income": float(max(0.0, export_revenue)),
        "eco_value": float(max(0.0, float(getattr(base_delta, "delta_eco_value", 0.0) or 0.0))),
        "served_load_revenue": float(max(0.0, served_load_revenue)),
        "export_revenue": float(max(0.0, export_revenue)),
        "avoided_market_purchase_value": float(max(0.0, avoided_market_purchase_value)),
    }
    income["total"] = float(income["object_income"] + income["market_income"] + income["eco_value"])

    expenses = {
        "entry_price": float(current_price),
        "contract_costs": float(
            max(0.0, float(getattr(base_delta, "delta_contracts", 0.0) or 0.0))
        ),
        "fuel_and_taxes": float(
            max(0.0, float(getattr(base_delta, "delta_fuel_and_taxes", 0.0) or 0.0))
        ),
        "storage_operating_cost": float(max(0.0, storage_operating_cost)),
        "market_purchase": float(
            max(
                0.0,
                float(getattr(base_delta, "delta_market_purchase_cost", market_delta) or 0.0),
            )
        ),
    }
    expenses["total"] = float(sum(expenses.values()))

    network_losses_total = float(
        max(0.0, float(getattr(base_delta, "delta_network_losses_cost", 0.0) or 0.0))
    )
    penalties_total = float(max(0.0, float(getattr(base_delta, "delta_penalties", 0.0) or 0.0)))
    risk_total = float(
        max(0.0, float(getattr(base_delta, "delta_risk_penalty", 0.0) or 0.0))
        + max(0.0, float(model_risk_premium or 0.0))
    )
    total_losses_and_risks = float(network_losses_total + penalties_total + risk_total)
    losses_and_risks = {
        "network_losses": network_losses_total,
        "penalties": penalties_total,
        "overload_penalties": float(max(0.0, overload_penalties)),
        "deficit_penalties": float(max(0.0, deficit_penalties)),
        "risk_total": risk_total,
        "flags": list(getattr(base_delta, "flags", [])),
        "total": total_losses_and_risks,
    }

    net_profit = float(income["total"] - expenses["total"] - total_losses_and_risks)
    roi = float(net_profit / current_price) if current_price > 0 else 0.0
    payback = float(current_price / net_profit) if net_profit > 0 else None
    result = {
        "utility_total": float(base_delta.delta_total),
        "net_profit": net_profit,
        "roi": roi,
        "payback_ratio": payback,
        "threshold_bid": float(hard_bid),
    }
    decomposition = {
        "served_load_revenue": float(max(0.0, served_load_revenue)),
        "avoided_market_purchase_value": float(max(0.0, avoided_market_purchase_value)),
        "export_revenue": float(max(0.0, export_revenue)),
        "fuel_and_taxes": expenses["fuel_and_taxes"],
        "storage_operating_cost": expenses["storage_operating_cost"],
        "network_loss_cost": losses_and_risks["network_losses"],
        "overload_penalties": losses_and_risks["overload_penalties"],
        "deficit_penalties": losses_and_risks["deficit_penalties"],
    }
    ui_rows: List[Dict[str, Any]] = []

    def push_row(
        *,
        key: str,
        label: str,
        value: float,
        group: str,
        always: bool = False,
        emphasis: bool = False,
    ) -> None:
        numeric = float(value)
        if not always and abs(numeric) <= 1e-6:
            return
        ui_rows.append(
            {
                "key": key,
                "label": label,
                "value": numeric,
                "group": group,
                "emphasis": bool(emphasis),
            }
        )

    push_row(
        key="served_load_revenue",
        label="Доход от объектов",
        value=income["served_load_revenue"],
        group="income",
    )
    push_row(
        key="export_revenue",
        label="Доход от экспорта",
        value=income["export_revenue"],
        group="income",
    )
    push_row(
        key="avoided_market_purchase_value",
        label="Эффект замещения рыночной покупки",
        value=income["avoided_market_purchase_value"],
        group="income",
    )
    push_row(
        key="eco_value",
        label="Экологический вклад",
        value=income["eco_value"],
        group="income",
    )
    push_row(
        key="entry_price",
        label="Цена входа",
        value=expenses["entry_price"],
        group="expense",
        always=True,
    )
    push_row(
        key="contract_costs",
        label="Контрактные расходы",
        value=expenses["contract_costs"],
        group="expense",
    )
    push_row(
        key="fuel_and_taxes",
        label="Топливо и налоги",
        value=expenses["fuel_and_taxes"],
        group="expense",
    )
    push_row(
        key="storage_operating_cost",
        label="Эксплуатация накопителя",
        value=expenses["storage_operating_cost"],
        group="expense",
    )
    push_row(
        key="market_purchase",
        label="Допзакупка на рынке",
        value=expenses["market_purchase"],
        group="expense",
    )
    push_row(
        key="network_losses",
        label="Сетевые потери",
        value=losses_and_risks["network_losses"],
        group="loss",
    )
    push_row(
        key="overload_penalties",
        label="Штрафы за перегруз",
        value=losses_and_risks["overload_penalties"],
        group="loss",
    )
    push_row(
        key="deficit_penalties",
        label="Штрафы за дефицит",
        value=losses_and_risks["deficit_penalties"],
        group="loss",
    )
    push_row(
        key="penalties",
        label="Штрафы всего",
        value=losses_and_risks["penalties"],
        group="loss",
    )
    push_row(
        key="risk_total",
        label="Риск-премия",
        value=losses_and_risks["risk_total"],
        group="loss",
    )
    push_row(
        key="net_profit",
        label="Итоговая чистая прибыль",
        value=net_profit,
        group="result",
        always=True,
        emphasis=True,
    )
    return {
        "income": income,
        "expenses": expenses,
        "losses_and_risks": losses_and_risks,
        "result": result,
        "decomposition": decomposition,
        "ui_rows": ui_rows,
    }


def resolve_working_bid(
    *,
    decision_summary: Dict[str, Any],
    financial_breakdown: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    summary = dict(decision_summary or {})
    breakdown = dict(financial_breakdown or {})
    result = dict(breakdown.get("result") or {})
    losses = dict(breakdown.get("losses_and_risks") or {})

    cautious_bid = max(0.0, _as_float(summary.get("cautious_bid"), 0.0))
    target_bid = max(0.0, _as_float(summary.get("target_bid"), 0.0))
    hard_ceiling_bid = max(0.0, _as_float(summary.get("hard_ceiling_bid"), 0.0))
    budget_adjusted_bid = max(
        0.0,
        _as_float(summary.get("budget_adjusted_bid"), 0.0),
    )
    model_working_bid = max(0.0, _as_float(summary.get("model_working_bid"), 0.0))
    remaining_budget = max(0.0, _as_float(summary.get("budget_remaining"), 0.0))
    expected_net_profit = _as_float(summary.get("expected_net_profit"), 0.0)
    risk_adjusted_net_profit = _as_float(summary.get("risk_adjusted_net_profit"), 0.0)
    net_profit = _as_float(result.get("net_profit"), 0.0)
    risk_total = _as_float(losses.get("risk_total"), 0.0)

    if remaining_budget <= 0.0:
        return {
            "working_bid": 0.0,
            "working_bid_source": "zero",
            "working_bid_reason": "Рабочая цена равна 0: бюджет сессии исчерпан.",
        }

    if expected_net_profit <= 0.0:
        return {
            "working_bid": 0.0,
            "working_bid_source": "zero",
            "working_bid_reason": (
                "Рабочая цена равна 0: взвешенная маржинальная прибыль неположительная, "
                "поэтому лот не даёт честной аукционной цены в текущем портфеле."
            ),
        }

    if budget_adjusted_bid > 0.0:
        chosen_bid = min(budget_adjusted_bid, model_working_bid) if model_working_bid > 0.0 else budget_adjusted_bid
        if budget_adjusted_bid + 1e-9 < target_bid:
            source = "budget_adjusted"
            if chosen_bid + 1e-9 < budget_adjusted_bid:
                reason = (
                    "Рабочая цена ограничена бюджетом и дополнительно снижена риск-буфером: "
                    "целевой bid не помещается в остаток и модель оставляет запас по сценарию."
                )
            else:
                reason = (
                    "Рабочая цена ограничена бюджетом: целевая ставка выше доступного остатка, "
                    "поэтому в аукцион идёт budget-adjusted цена."
                )
        else:
            source = "target"
            if chosen_bid + 1e-9 < target_bid:
                reason = (
                    "Рабочая цена ниже target: модель сохраняет риск-буфер между target и рабочей "
                    "ставкой, чтобы ставка оставалась реалистичной по всем сценариям."
                )
            else:
                reason = "Рабочая цена совпадает с целевой ставкой: экономика и бюджет не конфликтуют."
        return {
            "working_bid": float(chosen_bid),
            "working_bid_source": source,
            "working_bid_reason": reason,
        }

    if cautious_bid > 0.0 and remaining_budget + 1e-9 >= cautious_bid:
        return {
            "working_bid": float(cautious_bid),
            "working_bid_source": "cautious",
            "working_bid_reason": (
                "Рабочая цена переведена на осторожный уровень: target недоступен после учёта "
                "риска, синергии или бюджета."
            ),
        }

    if net_profit <= 0.0:
        reason = "Рабочая цена равна 0: ожидаемая чистая прибыль неположительная."
    elif risk_adjusted_net_profit <= 0.0:
        reason = (
            "Рабочая цена равна 0: после учёта риска и резервов лот не оставляет положительной "
            "маржинальной прибыли."
        )
    elif hard_ceiling_bid > 0.0 and remaining_budget + 1e-9 < cautious_bid:
        reason = (
            "Рабочая цена равна 0: даже осторожная ставка выше доступного остатка бюджета; "
            "лот можно только пропустить."
        )
    elif risk_total > 0.0:
        reason = "Рабочая цена равна 0: риск-премия перекрывает экономический эффект."
    else:
        reason = "Рабочая цена равна 0: лот не формирует допустимую ставку в текущем контексте."
    return {
        "working_bid": 0.0,
        "working_bid_source": "zero",
        "working_bid_reason": reason,
    }


def _valuation_model_v3(
    *,
    p_worst: float,
    p_base: float,
    p_best: float,
    p_exp: float,
    horizon_ticks: int,
    remaining_budget: float,
    evaluation_cfg: Dict[str, Any],
    role_profile: Dict[str, Any] | None = None,
    portfolio_synergy: float = 0.0,
    system_fit_score: float = 0.0,
) -> Dict[str, Any]:
    horizon = max(1, int(horizon_ticks or 1))
    risk_lambda = float(evaluation_cfg.get("risk_lambda", 0.25))
    volatility_lambda = float(evaluation_cfg.get("volatility_lambda", 0.15))
    reserve_margin_abs = float(evaluation_cfg.get("reserve_margin_abs", 5.0))
    reserve_margin_share = float(evaluation_cfg.get("reserve_margin_share", 0.10))

    scenario_volatility = float(
        pstdev([float(p_worst), float(p_base), float(p_best)])
        if len({float(p_worst), float(p_base), float(p_best)}) > 1
        else 0.0
    )
    downside_gap = max(0.0, float(p_base) - float(p_worst))
    risk_premium = float(risk_lambda * downside_gap + volatility_lambda * scenario_volatility)
    risk_ratio = float(risk_premium / max(abs(float(p_exp)), abs(float(p_base)), 1.0))

    if float(p_worst) <= 0.0 or risk_ratio >= 0.60:
        risk_band = "high"
    elif risk_ratio >= 0.30:
        risk_band = "medium"
    else:
        risk_band = "low"

    payback_ticks_map = {"low": 25, "medium": 20, "high": 15}
    cap_share_map = {"low": 0.30, "medium": 0.24, "high": 0.18}
    cautious_share_map = {"low": 0.40, "medium": 0.32, "high": 0.24}
    risk_buffer_map = {"low": 0.30, "medium": 0.55, "high": 0.85}
    working_share_map = {"low": 1.00, "medium": 0.88, "high": 0.70}

    role_payload = dict(role_profile or {})
    role_multipliers = dict(role_payload.get("multipliers") or {})
    target_multiplier = float(role_multipliers.get("target", 1.0) or 1.0)
    cautious_multiplier = float(role_multipliers.get("cautious", 1.0) or 1.0)
    ceiling_multiplier = float(role_multipliers.get("ceiling", 1.0) or 1.0)

    payback_ticks = int(payback_ticks_map[risk_band])
    cap_share = float(cap_share_map[risk_band])
    synergy_bonus = _clamp(float(portfolio_synergy), -abs(float(p_base)) * 0.25, abs(float(p_base)) * 0.25)
    system_bonus = _clamp(float(system_fit_score), -abs(float(p_base)) * 0.20, abs(float(p_base)) * 0.20)

    positive_expected = max(0.0, float(p_exp))
    positive_base = max(0.0, float(p_base))
    positive_best = max(0.0, float(p_best))
    payback_value = positive_expected / float(horizon) * float(payback_ticks)
    capped_value = positive_expected * cap_share
    anchor_value = max(0.0, min(payback_value, capped_value))

    reserve_margin = float(max(reserve_margin_abs, reserve_margin_share * positive_expected))
    risk_buffer = float(risk_premium * risk_buffer_map[risk_band])

    cautious_base = max(0.0, min(anchor_value * 0.55, max(0.0, float(p_worst)) * cautious_share_map[risk_band]))
    cautious_bid = max(0.0, cautious_base * cautious_multiplier)

    target_core = max(0.0, positive_expected - risk_premium - reserve_margin)
    if target_core <= 0.0 and positive_expected > 0.0 and positive_base > 0.0:
        base_support = max(0.0, positive_base - reserve_margin * 0.35 - risk_premium * 0.10)
        target_core = min(
            positive_base,
            max(0.0, base_support * 0.35 + anchor_value * 0.25 + positive_best * 0.08),
        )

    if positive_expected > 0.0:
        target_candidate = (
            target_core
            + max(0.0, synergy_bonus) * 0.20
            - max(0.0, -synergy_bonus) * 0.45
            + max(0.0, system_bonus) * 0.15
            - max(0.0, -system_bonus) * 0.40
        )
        target_candidate = min(positive_base or target_candidate, max(0.0, target_candidate))
    else:
        target_candidate = 0.0
    target_bid = max(cautious_bid, target_candidate * target_multiplier)

    if positive_expected > 0.0 and target_bid > 0.0:
        ceiling_candidate = max(
            target_bid,
            positive_base - 0.10 * reserve_margin + max(0.0, synergy_bonus) * 0.12 + max(0.0, system_bonus) * 0.08,
        )
        hard_ceiling_bid = max(target_bid, ceiling_candidate * ceiling_multiplier)
    else:
        hard_ceiling_bid = 0.0

    budget_adjusted_bid = min(target_bid, max(0.0, float(remaining_budget)))
    working_share = float(working_share_map[risk_band])
    if budget_adjusted_bid > cautious_bid:
        working_candidate = cautious_bid + (budget_adjusted_bid - cautious_bid) * working_share
    else:
        working_candidate = budget_adjusted_bid
    working_bid = max(0.0, min(budget_adjusted_bid, working_candidate))
    risk_adjusted_net_profit = float(
        float(p_exp) - risk_premium - reserve_margin + 0.30 * synergy_bonus + 0.20 * system_bonus
    )

    if positive_expected <= 0.0:
        cautious_bid = 0.0
        target_bid = 0.0
        hard_ceiling_bid = 0.0
        budget_adjusted_bid = 0.0
        working_bid = 0.0

    return {
        "model": "valuation_model_v3",
        "profile": str(role_payload.get("dominant_role") or "mixed"),
        "risk_band": risk_band,
        "horizon_ticks": int(horizon),
        "p_worst": float(p_worst),
        "p_base": float(p_base),
        "p_best": float(p_best),
        "p_exp": float(p_exp),
        "scenario_volatility": float(scenario_volatility),
        "downside_gap": float(downside_gap),
        "risk_ratio": float(risk_ratio),
        "risk_premium": float(risk_premium),
        "payback_ticks": int(payback_ticks),
        "cap_share": float(cap_share),
        "role_multipliers": {
            "target": float(target_multiplier),
            "cautious": float(cautious_multiplier),
            "ceiling": float(ceiling_multiplier),
        },
        "portfolio_synergy": float(portfolio_synergy),
        "system_fit_score": float(system_fit_score),
        "anchor_value": float(anchor_value),
        "synergy_bonus": float(synergy_bonus),
        "system_bonus": float(system_bonus),
        "reserve_margin": float(reserve_margin),
        "risk_buffer": float(risk_buffer),
        "cautious_bid": float(cautious_bid),
        "target_bid": float(target_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "working_bid": float(working_bid),
        "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
    }


def _latest_eval_state(session: GameSession, lot: Lot) -> Dict[str, Any]:
    latest = (
        db.session.query(EvaluationResult)
        .filter_by(session_id=session.id, lot_id=lot.id)
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .first()
    )
    if latest is None:
        return {"is_stale": False, "stale_reason": ""}
    return {"is_stale": bool(latest.is_stale), "stale_reason": latest.stale_reason or ""}


def _simulate_portfolio_with_lots(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    forecast: Optional[Forecast],
    cfg: Dict[str, Any],
    portfolio_lots: Sequence[Lot] | None = None,
) -> Tuple[
    Dict[str, ScenarioSnapshot], List[int], Dict[str, Dict[int, float]], Dict[str, Dict[int, float]]
]:
    if forecast is not None:
        factors, profiles, ticks = _period_series(list(forecast.periods))
    else:
        factors, profiles, ticks = _bundled_series()
    simulated_lots = [*list(portfolio_lots or []), *list(lots)]
    assets = _collect_assets(session, simulated_lots)
    snapshots = {
        "base": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="base"
        ),
        "worst": _simulate_scenario(
            assets=assets,
            factors=factors,
            profiles=profiles,
            ticks=ticks,
            cfg=cfg,
            scenario="worst",
        ),
        "best": _simulate_scenario(
            assets=assets, factors=factors, profiles=profiles, ticks=ticks, cfg=cfg, scenario="best"
        ),
    }
    return snapshots, ticks, factors, profiles


def _build_delta_pack(
    base_state: Dict[str, ScenarioSnapshot], with_state: Dict[str, ScenarioSnapshot]
) -> Dict[str, DeltaSnapshot]:
    return {
        "base": _to_delta(base_state["base"], with_state["base"]),
        "worst": _to_delta(base_state["worst"], with_state["worst"]),
        "best": _to_delta(base_state["best"], with_state["best"]),
    }


def evaluate_lot_bundle(
    *,
    session: GameSession,
    lots: Sequence[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    portfolio_lots: Sequence[Lot] | None = None,
    reserved_spend: float = 0.0,
) -> Dict[str, Any]:
    rules_cfg = dict(session.ruleset.config_json or {})
    selected_strategy = strategy or session.selected_strategy
    analysis_ctx = resolve_analysis_context(
        session, forecast_id=forecast.id if forecast is not None else None
    )
    forecast = analysis_ctx["forecast"]
    forecast_summary = dict(analysis_ctx["forecast_summary"])
    compatibility = dict(forecast_summary.get("compatibility_report") or {})
    if not bool(forecast_summary.get("is_compatible", True)):
        raise ForecastCompatibilityError(compatibility)

    portfolio_lots = list(portfolio_lots or [])
    candidate_lots = list(lots)
    portfolio_reserved_spend = _reserved_lot_spend(portfolio_lots)
    total_reserved_spend = float(max(0.0, reserved_spend) + portfolio_reserved_spend)

    base_state, ticks, factors, profiles = _simulate_portfolio_with_lots(
        session=session,
        lots=[],
        forecast=forecast,
        cfg=rules_cfg,
        portfolio_lots=portfolio_lots,
    )
    with_state, _, _, _ = _simulate_portfolio_with_lots(
        session=session,
        lots=candidate_lots,
        forecast=forecast,
        cfg=rules_cfg,
        portfolio_lots=portfolio_lots,
    )
    deltas = _build_delta_pack(base_state, with_state)

    d_base = deltas["base"]
    d_worst = deltas["worst"]
    d_best = deltas["best"]

    remaining_budget = _remaining_budget(session, reserved_spend=total_reserved_spend)
    entry_price_total = float(sum(_lot_reference_price(lot) for lot in candidate_lots))

    lot_assets = _collect_lot_assets(candidate_lots)
    standalone_state = _simulate_assets(
        assets=lot_assets,
        factors=factors,
        profiles=profiles,
        ticks=ticks,
        cfg=rules_cfg,
    )
    role_profile = _lot_role_profile(candidate_lots)
    system_check = _lot_connection_outlook(session=session, lots=candidate_lots)

    pwin = float(((rules_cfg.get("auction", {}) or {}).get("pwin_default", 0.35)))
    scenario_breakdown = {
        "worst": _scenario_row(
            label="Worst",
            scenario="worst",
            delta_obj=d_worst,
            current_price=entry_price_total,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
        "base": _scenario_row(
            label="Base",
            scenario="base",
            delta_obj=d_base,
            current_price=entry_price_total,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
        "best": _scenario_row(
            label="Best",
            scenario="best",
            delta_obj=d_best,
            current_price=entry_price_total,
            pwin=pwin,
            remaining_budget=remaining_budget,
        ),
    }

    net_profit_worst = float(scenario_breakdown["worst"]["net_profit"])
    net_profit_base = float(scenario_breakdown["base"]["net_profit"])
    net_profit_best = float(scenario_breakdown["best"]["net_profit"])
    expected_net_profit = _weighted_expected(
        rules_cfg,
        base=net_profit_base,
        worst=net_profit_worst,
        best=net_profit_best,
    )
    standalone_net_profit_base = float(standalone_state["base"].utility_score - entry_price_total)
    standalone_net_profit_worst = float(standalone_state["worst"].utility_score - entry_price_total)
    standalone_net_profit_best = float(standalone_state["best"].utility_score - entry_price_total)
    standalone_expected_net_profit = _weighted_expected(
        rules_cfg,
        base=standalone_net_profit_base,
        worst=standalone_net_profit_worst,
        best=standalone_net_profit_best,
    )
    portfolio_synergy = float(expected_net_profit - standalone_expected_net_profit)

    evaluation_cfg = dict(rules_cfg.get("evaluation", {}) or {})
    valuation_model = _valuation_model_v3(
        p_worst=float(net_profit_worst),
        p_base=float(net_profit_base),
        p_best=float(net_profit_best),
        p_exp=float(expected_net_profit),
        horizon_ticks=len(ticks),
        remaining_budget=float(remaining_budget),
        evaluation_cfg=evaluation_cfg,
        role_profile=role_profile,
        portfolio_synergy=portfolio_synergy,
        system_fit_score=float(system_check.get("system_fit_score", 0.0) or 0.0),
    )
    cautious_bid = float(valuation_model["cautious_bid"])
    target_bid = float(valuation_model["target_bid"])
    hard_ceiling_bid = float(valuation_model["hard_ceiling_bid"])
    budget_adjusted_bid = float(valuation_model["budget_adjusted_bid"])
    risk_premium = float(valuation_model["risk_premium"])
    reserve_margin = float(valuation_model["reserve_margin"])
    risk_adjusted_net_profit = float(valuation_model["risk_adjusted_net_profit"])
    if bool(system_check.get("topology_invalid")):
        cautious_bid = 0.0
        target_bid = 0.0
        hard_ceiling_bid = 0.0
        budget_adjusted_bid = 0.0
        risk_adjusted_net_profit = min(0.0, float(risk_adjusted_net_profit))
        valuation_model["cautious_bid"] = 0.0
        valuation_model["target_bid"] = 0.0
        valuation_model["hard_ceiling_bid"] = 0.0
        valuation_model["budget_adjusted_bid"] = 0.0
        valuation_model["working_bid"] = 0.0
        valuation_model["risk_adjusted_net_profit"] = float(risk_adjusted_net_profit)

    weights = strategy_weights(rules_cfg, selected_strategy)
    delta_profit = (
        float(d_base.delta_income)
        - float(d_base.delta_penalties)
        - float(d_base.delta_contracts)
        - float(d_base.delta_market_net)
        - float(d_base.delta_fuel_and_taxes)
        - float(d_base.delta_storage_operating_cost)
        - float(d_base.delta_network_losses_cost)
    )
    delta_balance = float(-d_base.delta_penalties)
    delta_network_stability = float(-d_base.delta_risk_penalty - d_base.delta_network_losses_cost)
    delta_storage_flex = float(
        d_base.role_breakdown.get("storage", 0.0) + d_base.role_breakdown.get("infrastructure", 0.0)
    )
    delta_green = float(d_base.delta_eco_points)
    delta_risk = float(max(0.0, d_base.delta_total - d_worst.delta_total))
    score = (
        float(weights["w1_economy"]) * delta_profit
        + float(weights["w2_balance"]) * delta_balance
        + float(weights["w3_stability"]) * delta_network_stability
        + float(weights["w4_green"]) * delta_green
        + float(weights["w5_flex"]) * delta_storage_flex
        - float(weights["w6_risk"]) * delta_risk
    )
    if bool(system_check.get("topology_invalid")):
        score = 0.0

    confidence = _clamp(
        1.0 - min(0.6, abs(d_best.delta_total - d_worst.delta_total) / 1200.0), 0.0, 1.0
    )
    financial_breakdown = _financial_breakdown(
        base_delta=d_base,
        current_price=entry_price_total,
        hard_bid=hard_ceiling_bid,
        model_risk_premium=risk_premium,
    )
    reasons = _human_reasons(d_base, top_k=5)
    risk_commentary = (
        "Риск контролируемый: прогноз совместим, запас по худшему сценарию положительный."
        if valuation_model["risk_band"] == "low"
        else "Риск повышен: чувствительность к сценариям требует более осторожной ставки."
    )
    if bool(system_check.get("topology_invalid")):
        risk_commentary = (
            "Оценка заблокирована: в энергосистеме есть структурные ошибки "
            "(цикл/недостижимые объекты/критичные лимиты)."
        )
    elif str(system_check.get("status") or "") == "blocked":
        risk_commentary = (
            "Риск повышен: часть объектов не проходит по сетевым лимитам, поэтому рабочая цена "
            "дополнительно снижена."
        )
    if bool(system_check.get("topology_invalid")):
        strategy_fit_text = (
            f"{strategy_label(selected_strategy)}: расчёт заблокирован из-за некорректной топологии сети."
        )
    else:
        strategy_fit_text = (
            f"{strategy_label(selected_strategy)}: приоритет риск-скорректированной прибыли соблюдается."
            if target_bid > 0
            else f"{strategy_label(selected_strategy)}: лот не поддерживает рабочую ставку в текущих условиях."
        )

    metrics = {
        "delta_score": float(d_base.delta_total),
        "delta_profit": float(delta_profit),
        "delta_balance": float(delta_balance),
        "delta_green_score": float(delta_green),
        "delta_storage_flexibility": float(delta_storage_flex),
        "delta_network_stability": float(delta_network_stability),
        "delta_risk": float(delta_risk),
        "weighted_expected": float(expected_net_profit),
        "weights": weights,
        "scenario_delta": {
            "base": asdict(d_base),
            "worst": asdict(d_worst),
            "best": asdict(d_best),
        },
        "scenarios": dict(scenario_breakdown),
        "decomposition": dict(financial_breakdown.get("decomposition") or {}),
        "bids": {
            "cautious_bid": float(cautious_bid),
            "target_bid": float(target_bid),
            "hard_ceiling_bid": float(hard_ceiling_bid),
            "budget_adjusted_bid": float(budget_adjusted_bid),
            "budget_remaining": float(remaining_budget),
            "expected_net_profit": float(expected_net_profit),
            "model_working_bid": float(valuation_model["working_bid"]),
            "risk_premium": float(risk_premium),
            "reserve_margin": float(reserve_margin),
            "portfolio_synergy": float(portfolio_synergy),
            "system_fit_score": float(system_check.get("system_fit_score", 0.0) or 0.0),
            "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
            "valuation_basis": "fair_value",
            "valuation_model": valuation_model,
        },
        "portfolio_delta": {
            "net_profit_base": float(net_profit_base),
            "net_profit_worst": float(net_profit_worst),
            "net_profit_best": float(net_profit_best),
            "standalone_net_profit_base": float(standalone_net_profit_base),
            "standalone_net_profit_worst": float(standalone_net_profit_worst),
            "standalone_net_profit_best": float(standalone_net_profit_best),
            "standalone_expected_net_profit": float(standalone_expected_net_profit),
            "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
            "utility_base": float(d_base.delta_total),
            "horizon_ticks": int(len(ticks)),
        },
        "forecast_compatibility": compatibility,
        "role_breakdown": dict(d_base.role_breakdown),
        "synergy": {
            "score": float(portfolio_synergy),
            "standalone_expected_net_profit": float(standalone_expected_net_profit),
            "marginal_expected_net_profit": float(expected_net_profit),
        },
        "system_check": dict(system_check),
        "role_profile": dict(role_profile),
    }

    decision_summary = {
        "cautious_bid": float(cautious_bid),
        "target_bid": float(target_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "budget_remaining": float(remaining_budget),
        "expected_net_profit": float(expected_net_profit),
        "risk_adjusted_net_profit": float(risk_adjusted_net_profit),
        "model_working_bid": float(valuation_model["working_bid"]),
        "portfolio_synergy": float(portfolio_synergy),
        "system_fit_score": float(system_check.get("system_fit_score", 0.0) or 0.0),
    }
    working_bid_payload = resolve_working_bid(
        decision_summary=decision_summary,
        financial_breakdown=financial_breakdown,
    )
    decision_summary.update(working_bid_payload)
    metrics["bids"].update(working_bid_payload)

    return {
        "summary_score": float(score),
        "scenario_breakdown": scenario_breakdown,
        "financial_breakdown": financial_breakdown,
        "decision_summary": decision_summary,
        "reasons": reasons,
        "explanation": " ".join(reasons) if reasons else "Нет подробного объяснения.",
        "risk_commentary": risk_commentary,
        "strategy_fit_text": strategy_fit_text,
        "confidence": float(confidence),
        "metrics": metrics,
        "system_check": dict(system_check),
        "role_profile": dict(role_profile),
        "forecast_context": dict(analysis_ctx["forecast_context"]),
        "forecast_summary": forecast_summary,
        "analysis_context": {
            "mode": "forecast",
            "mode_label": "С прогнозом",
            "source": analysis_ctx["forecast_context"]["source"],
            "source_label": analysis_ctx["forecast_context"]["source_label"],
            "forecast_id": analysis_ctx["forecast_context"]["forecast_id"],
            "forecast_name": analysis_ctx["forecast_context"]["forecast_name"],
        },
        "forecast_compatibility": compatibility,
        "portfolio_context": _portfolio_context(
            session,
            reserved_spend=total_reserved_spend,
            extra_portfolio_lots=portfolio_lots,
        ),
        "recommended_bid_soft": float(cautious_bid),
        "recommended_bid_hard": float(target_bid),
        "cautious_bid": float(cautious_bid),
        "target_bid": float(target_bid),
        "hard_ceiling_bid": float(hard_ceiling_bid),
        "budget_adjusted_bid": float(budget_adjusted_bid),
        "working_bid": float(working_bid_payload["working_bid"]),
        "working_bid_source": str(working_bid_payload["working_bid_source"]),
        "working_bid_reason": str(working_bid_payload["working_bid_reason"]),
    }


def evaluate_lot(
    *,
    session: GameSession,
    lot: Lot,
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    mode: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    payload = evaluate_lot_bundle(session=session, lots=[lot], strategy=strategy, forecast=forecast)
    stale_state = _latest_eval_state(session, lot)
    payload.update(
        {
            "session_id": session.id,
            "lot_id": lot.id,
            "is_stale": bool(stale_state["is_stale"]),
            "stale_reason": stale_state["stale_reason"],
            "score_definition": (
                "Итоговая полезность: риск-скорректированная прибыль, стабильность, гибкость и совместимость прогноза."
            ),
        }
    )

    if persist:
        row = EvaluationResult(
            session_id=session.id,
            lot_id=lot.id,
            mode="forecast",
            scenario="base",
            summary_score=float(payload["summary_score"]),
            metrics_json={
                **payload["metrics"],
                "analysis_context": payload["analysis_context"],
                "forecast_context": payload["forecast_context"],
                "forecast_summary": payload["forecast_summary"],
                "portfolio_context": payload["portfolio_context"],
                "scenario_breakdown": payload["scenario_breakdown"],
                "financial_breakdown": payload["financial_breakdown"],
                "decision_summary": payload["decision_summary"],
                "reasons": payload["reasons"],
                "risk_commentary": payload["risk_commentary"],
                "strategy_fit_text": payload["strategy_fit_text"],
            },
            explanation=payload["explanation"],
            recommended_bid_soft=float(payload["recommended_bid_soft"]),
            recommended_bid_hard=float(payload["recommended_bid_hard"]),
            confidence=float(payload["confidence"]),
            is_stale=False,
            stale_reason="",
            stale_marked_at=None,
        )
        db.session.add(row)
        db.session.commit()
        payload["evaluation_id"] = row.id
        payload["is_stale"] = False
        payload["stale_reason"] = ""
    return payload


def rank_lots(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
    persist: bool = False,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for lot in lots:
        results.append(
            evaluate_lot(
                session=session,
                lot=lot,
                strategy=strategy,
                forecast=forecast,
                persist=persist,
            )
        )
    results.sort(
        key=lambda item: (
            float(
                ((item.get("metrics") or {}).get("portfolio_delta") or {}).get(
                    "risk_adjusted_net_profit",
                    0.0,
                )
            ),
            float(item.get("summary_score", 0.0)),
        ),
        reverse=True,
    )
    return results


def recommend_best_lot(
    *,
    session: GameSession,
    lots: Iterable[Lot],
    strategy: Optional[str] = None,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    available_lots = [lot for lot in lots if str(lot.status or "") == "available"]
    ranked = rank_lots(
        session=session, lots=available_lots, strategy=strategy, forecast=forecast, persist=False
    )
    if not ranked:
        analysis_ctx = resolve_analysis_context(
            session, forecast_id=forecast.id if forecast else None
        )
        return {
            "best": None,
            "alternatives": [],
            "text": "Нет доступных лотов для рекомендации.",
            "forecast_context": analysis_ctx["forecast_context"],
            "portfolio_context": _portfolio_context(session),
        }
    best = ranked[0]
    alternatives = ranked[1:4]
    strategy_name = strategy_label(strategy or session.selected_strategy)
    text = (
        f"Лучший доступный лот для стратегии «{strategy_name}»: "
        f"полезность {best['summary_score']:.1f}, рабочая ставка {best['working_bid']:.1f}."
    )
    return {
        "best": best,
        "alternatives": alternatives,
        "recommended_bid": best["working_bid"],
        "decision_summary": best["decision_summary"],
        "strategy": strategy or session.selected_strategy,
        "text": text,
        "forecast_context": best["forecast_context"],
        "portfolio_context": best["portfolio_context"],
    }


def strategy_fit(
    *,
    session: GameSession,
    lot: Lot,
    forecast: Optional[Forecast] = None,
) -> Dict[str, Any]:
    strategies = [
        "generation",
        "consumer",
        "balanced",
        "storage",
        "eco",
        "risk_averse",
        "aggressive",
    ]
    rows: List[Dict[str, Any]] = []
    for strategy in strategies:
        out = evaluate_lot(
            session=session, lot=lot, strategy=strategy, forecast=forecast, persist=False
        )
        rows.append(
            {
                "strategy": strategy,
                "summary_score": out["summary_score"],
                "recommended_bid_hard": out["recommended_bid_hard"],
                "confidence": out["confidence"],
                "reason": out["explanation"],
                "forecast_context": out["forecast_context"],
                "portfolio_context": out["portfolio_context"],
            }
        )
    rows.sort(key=lambda item: float(item["summary_score"]), reverse=True)
    analysis_ctx = resolve_analysis_context(session, forecast_id=forecast.id if forecast else None)
    return {
        "lot_id": lot.id,
        "rows": rows,
        "best_strategy": rows[0]["strategy"] if rows else None,
        "forecast_context": (
            rows[0]["forecast_context"] if rows else analysis_ctx["forecast_context"]
        ),
        "portfolio_context": rows[0]["portfolio_context"] if rows else _portfolio_context(session),
    }


__all__ = [
    "ForecastCompatibilityError",
    "_financial_breakdown",
    "_scenario_row",
    "evaluate_lot",
    "evaluate_lot_bundle",
    "rank_lots",
    "recommend_best_lot",
    "strategy_fit",
]
