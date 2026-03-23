from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .config import anti_dumping_cap_mw, ies2026_config, scenario_coefficients
from .forecast import ForecastDataset, dataset_from_pack
from .network import ensure_terminals, loss_fraction_for_object, plan_network, validate_network
from .types import (
    AuctionDirection,
    EnergyObject,
    ForecastTick,
    LotEvaluation,
    LotProfile,
    MarketBid,
    NetworkValidationReport,
    RiskBand,
    ScenarioReport,
    SimulationResult,
    SimulationTotals,
    StorageValueBreakdown,
)


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum() or ch == "_")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _mean(values: Iterable[float]) -> float:
    rows = [float(value) for value in values]
    if not rows:
        return 0.0
    return float(sum(rows) / len(rows))


def _risk_band(score: float) -> RiskBand:
    if score >= 0.55:
        return "high"
    if score >= 0.22:
        return "medium"
    return "low"


def _consumer_key(code: str) -> str:
    normalized = _norm(code)
    mapping = {
        "house_a": "house_a",
        "house_b": "house_b",
        "office": "office",
        "factory": "factory",
        "hospital": "hospital",
    }
    return mapping.get(normalized, "house_a")


def _auction_direction(objects: Sequence[EnergyObject]) -> AuctionDirection:
    candidate_categories = {obj.category for obj in objects if obj.is_candidate}
    if candidate_categories and candidate_categories <= {"consumer"}:
        return "descending_consumer_tariff"
    return "ascending_service_tariff"


def _consumer_tariff(obj: EnergyObject) -> float:
    params = dict(obj.parameters or {})
    return _as_float(
        params.get(
            "tariff_rub_per_tick",
            params.get(
                "tariff_rub_per_mw_tick",
                params.get("service_tariff_rub_per_tick", 0.0),
            ),
        ),
        0.0,
    )


def _service_cost_per_tick(obj: EnergyObject) -> float:
    params = dict(obj.parameters or {})
    if obj.category == "consumer":
        return _as_float(params.get("maintenance_cost_per_tick", 0.0), 0.0)
    return _as_float(
        params.get("contract_rub_per_tick", params.get("service_tariff_rub_per_tick", 0.0)),
        0.0,
    )


def _consumer_demand_multiplier(obj: EnergyObject, config: Dict[str, Any]) -> float:
    params = dict(obj.parameters or {})
    elasticity_cfg = dict((config.get("consumer") or {}).get("demand_elasticity") or {})
    key = _consumer_key(obj.code)
    profile = dict(elasticity_cfg.get(key) or {})
    reference_tariff = _as_float(
        params.get("reference_tariff", profile.get("reference_tariff", _consumer_tariff(obj) or 1.0)),
        1.0,
    )
    elasticity = _as_float(params.get("elasticity", profile.get("elasticity", 0.1)), 0.1)
    min_multiplier = _as_float(profile.get("min_multiplier", 0.8), 0.8)
    tariff = _consumer_tariff(obj)
    if reference_tariff <= 0:
        return 1.0
    if tariff <= reference_tariff:
        gain = min(0.2, (reference_tariff - tariff) / reference_tariff * elasticity * 0.5)
        return 1.0 + gain
    reduction = (tariff - reference_tariff) / reference_tariff * elasticity
    return max(min_multiplier, 1.0 - reduction)


def _consumer_penalty_rate(obj: EnergyObject, config: Dict[str, Any]) -> float:
    penalties = dict((config.get("consumer") or {}).get("unserved_penalty_per_mw_tick") or {})
    return _as_float(penalties.get(_consumer_key(obj.code), 4.0), 4.0)


def _consumer_base_demand(obj: EnergyObject, tick: ForecastTick, scenario: Dict[str, float]) -> float:
    params = dict(obj.parameters or {})
    key = _consumer_key(obj.code)
    profile_scale = _as_float(params.get("forecast_sensitivity", 1.0), 1.0)
    fallback = _as_float(params.get("expected_consumption_mw", 0.0), 0.0)
    forecast = _as_float(tick.demand_by_type.get(key, fallback), fallback)
    return max(0.0, forecast * profile_scale * _as_float(scenario.get("load", 1.0), 1.0))


def _solar_output(obj: EnergyObject, tick: ForecastTick, scenario: Dict[str, float], config: Dict[str, Any]) -> float:
    params = dict(obj.parameters or {})
    defaults = dict((config.get("generation") or {}).get("solar") or {})
    capacity = _as_float(params.get("generation_mw", params.get("capacity_mw", defaults.get("default_capacity_mw", 20.0))), 20.0)
    efficiency = _as_float(params.get("efficiency", defaults.get("default_efficiency", 0.94)), 0.94)
    illumination = max(0.0, float(tick.illumination) * _as_float(scenario.get("illumination", 1.0), 1.0))
    return max(0.0, min(capacity, capacity * illumination * efficiency))


def _wind_speed_for_object(obj: EnergyObject, tick: ForecastTick, scenario: Dict[str, float]) -> float:
    params = dict(obj.parameters or {})
    preferred = str(
        params.get("wind_channel")
        or params.get("wind_profile_key")
        or params.get("legacy_id")
        or ""
    ).strip()
    if preferred and preferred in tick.wind_channels:
        value = tick.wind_channels.get(preferred, 0.0)
    elif tick.wind_channels:
        value = _mean(tick.wind_channels.values())
    else:
        value = 0.0
    return max(0.0, float(value) * _as_float(scenario.get("wind", 1.0), 1.0))


def _wind_output(obj: EnergyObject, tick: ForecastTick, scenario: Dict[str, float], config: Dict[str, Any]) -> float:
    params = dict(obj.parameters or {})
    defaults = dict((config.get("generation") or {}).get("wind") or {})
    rated_power = _as_float(params.get("generation_mw", params.get("rated_power_mw", defaults.get("rated_power_mw", 18.0))), 18.0)
    cut_in = _as_float(params.get("cut_in_mps", defaults.get("cut_in_mps", 3.0)), 3.0)
    rated = _as_float(params.get("rated_mps", defaults.get("rated_mps", 11.0)), 11.0)
    cut_out = _as_float(params.get("cut_out_mps", defaults.get("cut_out_mps", 25.0)), 25.0)
    speed = _wind_speed_for_object(obj, tick, scenario)
    if speed < cut_in or speed >= cut_out:
        return 0.0
    if speed >= rated:
        return rated_power
    span = max(0.1, rated - cut_in)
    fraction = (speed - cut_in) / span
    return max(0.0, min(rated_power, rated_power * (fraction**3)))


def _usable_fraction(obj: EnergyObject, topology: NetworkValidationReport) -> float:
    return float(topology.usable_fraction_by_object.get(obj.object_id, 1.0) or 0.0)


def _aggregate_storage(objects: Sequence[EnergyObject], topology: NetworkValidationReport, config: Dict[str, Any]) -> Dict[str, float]:
    storage_cfg = dict(config.get("storage") or {})
    capacity = 0.0
    charge = 0.0
    discharge = 0.0
    efficiency = 0.0
    reserve_floor = 0.0
    loss = 0.0
    count = 0.0
    for obj in objects:
        if obj.category != "storage" or not obj.is_active:
            continue
        usable = _usable_fraction(obj, topology)
        if usable <= 0:
            continue
        params = dict(obj.parameters or {})
        capacity += _as_float(params.get("capacity_mw_tick", storage_cfg.get("capacity_mw_tick", 120.0)), 120.0) * usable
        charge += _as_float(params.get("charge_rate_mw_tick", params.get("charge_rate_mw", storage_cfg.get("charge_rate_mw_tick", 15.0))), 15.0) * usable
        discharge += _as_float(params.get("discharge_rate_mw_tick", params.get("discharge_rate_mw", storage_cfg.get("discharge_rate_mw_tick", 20.0))), 20.0) * usable
        efficiency += _as_float(params.get("roundtrip_efficiency", storage_cfg.get("roundtrip_efficiency", 0.93)), 0.93)
        reserve_floor += _as_float(params.get("reserve_floor_share", storage_cfg.get("reserve_floor_share", 0.2)), 0.2)
        loss += _as_float(topology.loss_fraction_by_object.get(obj.object_id, 0.0), 0.0)
        count += 1.0
    if count <= 0:
        return {"capacity": 0.0, "charge": 0.0, "discharge": 0.0, "efficiency": 1.0, "reserve_floor_share": 0.0, "loss": 0.0}
    return {
        "capacity": capacity,
        "charge": charge,
        "discharge": discharge,
        "efficiency": max(0.65, efficiency / count),
        "reserve_floor_share": max(0.0, min(0.7, reserve_floor / count)),
        "loss": max(0.0, min(0.35, loss / count)),
    }


def _estimate_declared_sales(
    *,
    objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    topology: NetworkValidationReport,
    config: Dict[str, Any],
) -> List[float]:
    market_cfg = dict(config.get("market") or {})
    risk_buffer = _as_float(market_cfg.get("sale_risk_buffer_mw", 1.5), 1.5)
    previous_useful_energy = 0.0
    declared: List[float] = []
    for tick in dataset.ticks:
        generation_root = 0.0
        demand_root = 0.0
        for obj in objects:
            if not obj.is_active or _usable_fraction(obj, topology) <= 0:
                continue
            loss_fraction = float(topology.loss_fraction_by_object.get(obj.object_id, 0.0) or 0.0)
            if obj.category == "generator":
                code = _norm(obj.code)
                if code == "solar":
                    output = _solar_output(obj, tick, {"illumination": 1.0, "wind": 1.0}, config)
                else:
                    output = _wind_output(obj, tick, {"wind": 1.0}, config)
                generation_root += output * (1.0 - loss_fraction)
            elif obj.category == "consumer":
                delivered = _consumer_base_demand(obj, tick, {"load": 1.0}) * _consumer_demand_multiplier(obj, config)
                demand_root += delivered / max(0.55, 1.0 - loss_fraction)
        raw_surplus = max(0.0, generation_root - demand_root - risk_buffer)
        current = min(
            raw_surplus,
            anti_dumping_cap_mw(previous_useful_energy, config),
        )
        declared.append(current)
        previous_useful_energy = max(0.0, generation_root - demand_root)
    return declared


def _simulate(
    *,
    objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
    scenario_label: str,
    declared_sales: Sequence[float] | None = None,
) -> SimulationResult:
    scenario = scenario_coefficients(config, scenario_label)
    topology = validate_network(objects)
    for obj in objects:
        topology.loss_fraction_by_object[obj.object_id] = loss_fraction_for_object(
            obj=obj,
            objects=objects,
            config=config,
        )

    totals = SimulationTotals()
    deficit_ticks: List[int] = []
    surplus_ticks: List[int] = []
    market_events: List[MarketBid] = []
    tick_rows: List[Dict[str, Any]] = []
    notes: List[str] = []

    storage_cfg = _aggregate_storage(objects, topology, config)
    market_cfg = dict(config.get("market") or {})
    low_sale_factor = _as_float(market_cfg.get("low_price_sale_factor", 0.55), 0.55)
    risk_buffer = _as_float(market_cfg.get("sale_risk_buffer_mw", 1.5), 1.5)
    soc = storage_cfg["capacity"] * 0.5
    future_buy_prices = [float(row.market_buy_price) for row in dataset.ticks]
    previous_useful_energy = 0.0

    for idx, tick in enumerate(dataset.ticks):
        tick_generation_root = 0.0
        tick_generation_gross = 0.0
        tick_load_root = 0.0
        tick_served_load = 0.0
        tick_gross_load = 0.0
        tick_loss = 0.0
        tick_consumer_revenue = 0.0
        tick_fixed_tariff_revenue = 0.0
        tick_service_cost = 0.0
        tick_unserved_penalty = 0.0

        buy_price = float(tick.market_buy_price) * _as_float(scenario.get("buy_price", 1.0), 1.0)
        sell_price = float(tick.market_sell_price) * _as_float(scenario.get("sell_price", 1.0), 1.0)
        balancing_price = float(tick.balancing_penalty_price)
        gp_price = sell_price * low_sale_factor

        for obj in objects:
            if not obj.is_active:
                continue
            usable = _usable_fraction(obj, topology)
            if obj.category != "consumer":
                tick_service_cost += _service_cost_per_tick(obj)
            if usable <= 0.0:
                if obj.category == "consumer":
                    lost = _consumer_base_demand(obj, tick, scenario)
                    tick_unserved_penalty += lost * _consumer_penalty_rate(obj, config)
                continue
            loss_fraction = float(topology.loss_fraction_by_object.get(obj.object_id, 0.0) or 0.0)

            if obj.category == "consumer":
                gross = _consumer_base_demand(obj, tick, scenario)
                demand = gross * _consumer_demand_multiplier(obj, config) * usable
                root_need = demand / max(0.55, 1.0 - loss_fraction)
                tick_gross_load += gross
                tick_served_load += demand
                tick_load_root += root_need
                tick_loss += max(0.0, root_need - demand)
                tick_fixed_tariff_revenue += _consumer_tariff(obj) * usable
                continue

            if obj.category == "generator":
                code = _norm(obj.code)
                if code == "solar":
                    gross_output = _solar_output(obj, tick, scenario, config)
                else:
                    gross_output = _wind_output(obj, tick, scenario, config)
                gross_output *= usable
                useful = gross_output * max(0.0, 1.0 - loss_fraction)
                tick_generation_gross += gross_output
                totals.total_generation_mw += gross_output
                tick_generation_root += useful
                tick_loss += max(0.0, gross_output - useful)
                continue

        totals.gross_load_mw += tick_gross_load
        totals.served_load_mw += tick_served_load
        totals.fixed_tariff_revenue += tick_fixed_tariff_revenue
        totals.consumer_revenue += tick_fixed_tariff_revenue
        totals.consumer_revenue += tick_consumer_revenue
        totals.service_cost += tick_service_cost
        totals.unmet_load_penalty += tick_unserved_penalty

        net_root = tick_generation_root - tick_load_root
        totals.useful_generation_mw += tick_generation_root

        # Storage acts as a balancing and arbitrage layer, not as an infinite buffer.
        if storage_cfg["capacity"] > 0.0:
            reserve_floor = storage_cfg["capacity"] * storage_cfg["reserve_floor_share"]
            future_window = future_buy_prices[idx + 1 : idx + 7]
            future_peak = max(future_window) if future_window else buy_price
            declared_sale_hint = 0.0
            if declared_sales is not None and idx < len(declared_sales):
                declared_sale_hint = float(declared_sales[idx] or 0.0)
            if net_root < 0.0 and soc > reserve_floor:
                discharge = min(
                    -net_root,
                    storage_cfg["discharge"],
                    max(0.0, soc - reserve_floor),
                )
                if discharge > 0:
                    soc -= discharge
                    useful = discharge * max(0.0, 1.0 - storage_cfg["loss"])
                    net_root += useful
                    totals.storage.balancing += useful * buy_price
            elif declared_sale_hint > max(0.0, net_root) and soc > reserve_floor:
                discharge = min(
                    declared_sale_hint - max(0.0, net_root),
                    storage_cfg["discharge"],
                    max(0.0, soc - reserve_floor),
                )
                if discharge > 0:
                    soc -= discharge
                    useful = discharge * max(0.0, 1.0 - storage_cfg["loss"])
                    net_root += useful
                    totals.storage.arbitrage += useful * sell_price
                    totals.storage.anti_dumping_support += useful * 0.25
            elif net_root > 0.0 and soc < storage_cfg["capacity"] and future_peak > sell_price * 1.05:
                charge = min(net_root, storage_cfg["charge"], storage_cfg["capacity"] - soc)
                if charge > 0:
                    soc += charge * storage_cfg["efficiency"]
                    net_root -= charge
                    totals.storage.reserve += charge * _as_float(
                        (config.get("storage") or {}).get("reserve_credit_per_mw_tick", 0.35),
                        0.35,
                    )

        useful_energy = max(0.0, net_root)
        anti_dump_cap = anti_dumping_cap_mw(previous_useful_energy, config)
        if declared_sales is None:
            declared_sale = min(
                anti_dump_cap,
                max(0.0, useful_energy - risk_buffer),
            )
        else:
            declared_sale_input = 0.0
            if idx < len(declared_sales):
                declared_sale_input = float(declared_sales[idx] or 0.0)
            declared_sale = min(
                anti_dump_cap,
                max(0.0, declared_sale_input),
            )
        shortfall = max(0.0, declared_sale - useful_energy)
        actual_high_sale = min(useful_energy, declared_sale)
        low_price_sale = max(0.0, useful_energy - actual_high_sale)
        exchange_revenue = actual_high_sale * sell_price
        gp_sale_revenue = low_price_sale * gp_price
        market_revenue = exchange_revenue + gp_sale_revenue
        gp_purchase = max(0.0, -net_root)
        purchase_cost = gp_purchase * buy_price
        balancing_penalty = shortfall * balancing_price

        totals.market_revenue += market_revenue
        totals.exchange_sale_revenue += exchange_revenue
        totals.guaranteed_sale_revenue += gp_sale_revenue
        totals.market_purchase_cost += purchase_cost
        totals.gp_purchase_cost += purchase_cost
        totals.balancing_penalty += balancing_penalty
        totals.loss_mw += tick_loss
        totals.loss_cost += tick_loss * buy_price
        totals.useful_energy_export_mw += useful_energy

        if net_root < 0.0:
            deficit_ticks.append(int(tick.tick))
        elif net_root > 0.0:
            surplus_ticks.append(int(tick.tick))

        market_events.append(
            MarketBid(
                tick=int(tick.tick),
                declared_mw=declared_sale,
                anti_dumping_cap_mw=anti_dump_cap,
                useful_energy_mw=useful_energy,
                actual_high_price_sale_mw=actual_high_sale,
                low_price_sale_mw=low_price_sale,
                shortfall_mw=shortfall,
                high_price=sell_price,
                low_price=gp_price,
                balancing_penalty_price=balancing_price,
                gp_purchase_mw=gp_purchase,
                exchange_price_band=f"{int(_as_float(market_cfg.get('exchange_price_min', 2.0), 2.0))}-{int(_as_float(market_cfg.get('exchange_price_max', 20.0), 20.0))}",
            )
        )
        tick_profit = (
            tick_fixed_tariff_revenue
            + exchange_revenue
            + gp_sale_revenue
            - tick_service_cost
            - purchase_cost
            - balancing_penalty
            - tick_loss * buy_price
            - tick_unserved_penalty
        )
        previous_useful_energy = useful_energy
        notes_fragment = []
        if shortfall > 0.0:
            notes_fragment.append("shortfall_vs_declared")
        if low_price_sale > 0.0:
            notes_fragment.append("gp_sale")
        if gp_purchase > 0.0:
            notes_fragment.append("gp_purchase")
        # Tick payload is consumed by SSR/UI and post-auction template export.
        tick_rows.append(
            {
            "tick": int(tick.tick),
            "gross_generation": round(float(tick_generation_gross), 4),
            "gross_demand": round(float(tick_gross_load), 4),
            "losses": round(float(tick_loss), 4),
            "useful_energy": round(float(useful_energy), 4),
            "declared_sale": round(float(declared_sale), 4),
            "realized_sale": round(float(actual_high_sale), 4),
            "gp_sale": round(float(low_price_sale), 4),
            "purchase_from_gp": round(float(gp_purchase), 4),
            "balancing_penalty": round(float(balancing_penalty), 4),
            "fixed_tariffs": round(float(tick_fixed_tariff_revenue), 4),
            "service_tariffs": round(float(tick_service_cost), 4),
            "tick_profit": round(float(tick_profit), 4),
            "anti_dumping_cap": round(float(anti_dump_cap), 4),
            "notes": notes_fragment,
            }
        )

    reserve_credit = soc * _as_float((config.get("storage") or {}).get("reserve_credit_per_mw_tick", 0.35), 0.35)
    totals.reserve_credit += reserve_credit
    totals.flexibility_credit += (
        totals.storage.arbitrage
        + totals.storage.balancing
        + totals.storage.reserve
        + totals.storage.anti_dumping_support
    )
    totals.direct_profit = (
        totals.consumer_revenue
        + totals.market_revenue
        + totals.flexibility_credit
        + totals.reserve_credit
        - totals.service_cost
        - totals.market_purchase_cost
        - totals.loss_cost
        - totals.balancing_penalty
        - totals.unmet_load_penalty
    )
    if topology.blocking:
        notes.append("Система имеет блокирующие ошибки топологии, расчёт содержит topology risk.")
    return SimulationResult(
        totals=totals,
        topology=topology,
        market_bids=market_events,
        tick_rows=tick_rows,
        deficit_ticks=deficit_ticks,
        surplus_ticks=surplus_ticks,
        notes=notes,
    )


def _clone_objects(objects: Iterable[EnergyObject]) -> List[EnergyObject]:
    return [deepcopy(obj) for obj in ensure_terminals(objects)]


def _candidate_tariff_total(objects: Sequence[EnergyObject], direction: AuctionDirection) -> float:
    total = 0.0
    for obj in objects:
        if not obj.is_candidate:
            continue
        if direction == "descending_consumer_tariff":
            total += _consumer_tariff(obj)
        else:
            total += _service_cost_per_tick(obj)
    return total


def _lot_profile(objects: Sequence[EnergyObject]) -> LotProfile:
    categories = {str(obj.category or "") for obj in objects if obj.is_candidate}
    if not categories:
        return "mixed"
    if len(categories) == 1:
        category = next(iter(categories))
        if category in {"consumer", "generator", "storage", "infrastructure"}:
            return category
    return "mixed"


def _set_candidate_tariff(objects: Sequence[EnergyObject], value: float, direction: AuctionDirection) -> None:
    candidate_targets = [obj for obj in objects if obj.is_candidate]
    if not candidate_targets:
        return
    per_object = float(value) / max(1, len(candidate_targets))
    for obj in candidate_targets:
        params = dict(obj.parameters or {})
        if direction == "descending_consumer_tariff":
            params["tariff_rub_per_tick"] = per_object
            params["tariff_rub_per_mw_tick"] = per_object
        else:
            params["contract_rub_per_tick"] = per_object
        obj.parameters = params


def _consumer_tariff_break_even(
    *,
    base_objects: Sequence[EnergyObject],
    candidate_objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
) -> float:
    direction = "descending_consumer_tariff"
    current_tariff = _candidate_tariff_total(candidate_objects, direction)
    zero_candidates = _clone_objects(candidate_objects)
    _set_candidate_tariff(zero_candidates, 0.0, direction)
    zero_delta = _direct_delta(
        base_objects=base_objects,
        candidate_objects=zero_candidates,
        dataset=dataset,
        config=config,
    )["expected_delta_profit"]
    if zero_delta >= 0.0:
        return 0.0

    low = 0.0
    high = max(current_tariff, 1.0)
    high_delta = _direct_delta(
        base_objects=base_objects,
        candidate_objects=candidate_objects,
        dataset=dataset,
        config=config,
    )["expected_delta_profit"]
    while high_delta < 0.0 and high < max(24.0, current_tariff * 4.0 + 2.0):
        high *= 1.35
        tuned_candidates = _clone_objects(candidate_objects)
        _set_candidate_tariff(tuned_candidates, high, direction)
        high_delta = _direct_delta(
            base_objects=base_objects,
            candidate_objects=tuned_candidates,
            dataset=dataset,
            config=config,
        )["expected_delta_profit"]
    for _ in range(18):
        mid = (low + high) / 2.0
        tuned_candidates = _clone_objects(candidate_objects)
        _set_candidate_tariff(tuned_candidates, mid, direction)
        delta = _direct_delta(
            base_objects=base_objects,
            candidate_objects=tuned_candidates,
            dataset=dataset,
            config=config,
        )["expected_delta_profit"]
        if delta >= 0.0:
            high = mid
        else:
            low = mid
    return round(high, 4)


def _provider_tariff_break_even(
    *,
    candidate_objects: Sequence[EnergyObject],
    expected_delta_profit: float,
    config: Dict[str, Any],
) -> float:
    horizon = int((config.get("time") or {}).get("horizon_ticks", 48) or 48)
    current = _candidate_tariff_total(candidate_objects, "ascending_service_tariff")
    return round(max(0.0, current + expected_delta_profit / max(1, horizon)), 4)


def _bundle_synergy_value(
    *,
    base_objects: Sequence[EnergyObject],
    candidate_objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
    direct_delta_profit: float,
) -> float:
    if len(candidate_objects) <= 1:
        return 0.0
    standalone = 0.0
    for obj in candidate_objects:
        standalone += float(
            _direct_delta(
                base_objects=base_objects,
                candidate_objects=[deepcopy(obj)],
                dataset=dataset,
                config=config,
            )["expected_delta_profit"]
        )
    return round(float(direct_delta_profit) - standalone, 4)


def _direct_delta(
    *,
    base_objects: Sequence[EnergyObject],
    candidate_objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    base_only = _clone_objects(base_objects)
    base_result = _simulate(
        objects=base_only,
        dataset=dataset,
        config=config,
        scenario_label="base",
    )
    planned_objects, planning = plan_network(
        existing_objects=base_objects,
        candidate_objects=candidate_objects,
    )
    with_candidate = _simulate(
        objects=planned_objects,
        dataset=dataset,
        config=config,
        scenario_label="base",
    )
    scenarios: Dict[str, ScenarioReport] = {}
    for label in ("worst", "base", "best"):
        baseline = _simulate(objects=base_only, dataset=dataset, config=config, scenario_label=label)
        candidate = _simulate(objects=planned_objects, dataset=dataset, config=config, scenario_label=label)
        scenarios[label] = ScenarioReport(
            label=label,
            delta_profit=round(candidate.totals.direct_profit - baseline.totals.direct_profit, 4),
            totals=deepcopy(candidate.totals),
            notes=list(candidate.notes),
        )

    expected = round(
        scenarios["base"].delta_profit * 0.55
        + scenarios["worst"].delta_profit * 0.25
        + scenarios["best"].delta_profit * 0.20,
        4,
    )
    return {
        "expected_delta_profit": expected,
        "base_result": with_candidate,
        "baseline_result": base_result,
        "topology": planning,
        "scenarios": scenarios,
    }


def _estimate_enabler_value(
    *,
    base_objects: Sequence[EnergyObject],
    candidate_objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
    follow_up_candidates: Sequence[Tuple[int, str, Sequence[EnergyObject]]],
) -> Tuple[float, List[str]]:
    if not any(obj.category == "infrastructure" for obj in candidate_objects):
        return 0.0, []
    unlocks: List[Tuple[float, str]] = []
    for follow_up_id, follow_up_name, follow_up_objects in follow_up_candidates:
        without_candidate = _direct_delta(
            base_objects=base_objects,
            candidate_objects=follow_up_objects,
            dataset=dataset,
            config=config,
        )
        with_candidate = _direct_delta(
            base_objects=[*base_objects, *_clone_objects(candidate_objects)],
            candidate_objects=follow_up_objects,
            dataset=dataset,
            config=config,
        )
        gain = float(with_candidate["expected_delta_profit"]) - float(without_candidate["expected_delta_profit"])
        if gain <= 0.0:
            continue
        if not without_candidate["topology"].blocking and gain < 2.0:
            continue
        unlocks.append((gain, f"Инфраструктура открывает лот «{follow_up_name}» (+{gain:.2f})."))
    unlocks.sort(key=lambda item: item[0], reverse=True)
    selected = unlocks[:3]
    return round(sum(item[0] for item in selected), 4), [item[1] for item in selected]


def evaluate_candidate_bundle(
    *,
    lot_id: int,
    lot_name: str,
    base_objects: Sequence[EnergyObject],
    candidate_objects: Sequence[EnergyObject],
    forecast_pack: Dict[str, Dict[str, Dict[int, float]]] | None,
    ruleset_config: Dict[str, Any] | None,
    follow_up_candidates: Sequence[Tuple[int, str, Sequence[EnergyObject]]] | None = None,
) -> LotEvaluation:
    config = ies2026_config(ruleset_config)
    dataset = dataset_from_pack(forecast_pack, config=config)
    candidate = _clone_objects(candidate_objects)
    direction = _auction_direction(candidate)
    lot_profile = _lot_profile(candidate)
    direct = _direct_delta(
        base_objects=base_objects,
        candidate_objects=candidate,
        dataset=dataset,
        config=config,
    )
    direct_delta = float(direct["expected_delta_profit"])
    bundle_synergy = _bundle_synergy_value(
        base_objects=base_objects,
        candidate_objects=candidate,
        dataset=dataset,
        config=config,
        direct_delta_profit=direct_delta,
    )
    enabler_value, enabler_notes = _estimate_enabler_value(
        base_objects=base_objects,
        candidate_objects=candidate,
        dataset=dataset,
        config=config,
        follow_up_candidates=list(follow_up_candidates or []),
    )
    expected_delta = round(direct_delta + enabler_value, 4)
    current_tariff = _candidate_tariff_total(candidate, direction)
    auction_cfg = dict(config.get("auction") or {})
    repeat_increment = _as_float(auction_cfg.get("repeat_increment", 0.1), 0.1)
    if direction == "descending_consumer_tariff":
        break_even = _consumer_tariff_break_even(
            base_objects=base_objects,
            candidate_objects=candidate,
            dataset=dataset,
            config=config,
        )
        competitiveness = _as_float((config.get("auction") or {}).get("consumer_competitiveness_share", 0.45), 0.45)
        recommended = max(break_even, current_tariff - max(0.0, current_tariff - break_even) * competitiveness)
        aggressive_floor = max(break_even, current_tariff - max(0.0, current_tariff - break_even) * 0.85)
        opening_bid = max(recommended, current_tariff)
        counter_bid = max(break_even, recommended - repeat_increment)
        hard_limit = break_even
        allpay_policy = (
            "Special-case All-Pay допустим только если лот дошёл до hard floor и правила "
            "перевели повторный тур в tie-break."
        )
    else:
        break_even = _provider_tariff_break_even(
            candidate_objects=candidate,
            expected_delta_profit=expected_delta,
            config=config,
        )
        margin = _as_float((config.get("auction") or {}).get("provider_margin_share", 0.18), 0.18)
        recommended = max(0.0, break_even * (1.0 - margin))
        aggressive_floor = None
        opening_bid = max(0.0, min(recommended, current_tariff if current_tariff > 0.0 else recommended))
        counter_bid = min(break_even, max(recommended, opening_bid + repeat_increment))
        hard_limit = break_even
        allpay_policy = (
            "Special-case All-Pay допустим только на фиксированном пакете или в tie-break у hard ceiling; "
            "обычный тарифный аукцион All-Pay не включает."
        )

    topology_report = direct["topology"]
    scenario_reports = direct["scenarios"]
    topology_score = min(1.0, len([issue for issue in topology_report.issues if issue.severity == "critical"]) * 0.35 + len([issue for issue in topology_report.issues if issue.severity == "warning"]) * 0.12)
    market_spread = abs(scenario_reports["best"].delta_profit - scenario_reports["worst"].delta_profit) / max(1.0, abs(expected_delta) + 1.0)
    balancing_score = float(direct["base_result"].totals.balancing_penalty) / max(1.0, abs(direct["base_result"].totals.direct_profit) + 1.0)
    loss_score = float(direct["base_result"].totals.loss_cost) / max(1.0, abs(direct["base_result"].totals.direct_profit) + 1.0)
    assumptions = [
        "Потребители платят фиксированный тариф подключения за каждый такт; фактическое потребление не умножается на этот тариф.",
        "Спрос потребителей учитывает параметризуемую модель эластичности, а не точную игровую формулу.",
        "Потери сети рассчитаны параметризуемой аппроксимацией по глубине дерева и точке подключения.",
        "Биржевой экспорт ограничен антидемпингом 1.2 * useful_energy_(t-1) + 10 и использует отдельный GP fallback для непроданного остатка.",
    ]
    if direction == "descending_consumer_tariff":
        explanation = (
            f"Лот рассматривается как потребительский объект с аукционом на понижение тарифа. "
            f"Минимально допустимый fixed tariff {break_even:.2f}, рекомендуемый walkdown {recommended:.2f}. "
            f"Выручка считается как фиксированная плата за подключение на каждом такте, а не как demand * tariff. "
            f"Ожидаемый delta-profit {expected_delta:.2f} при текущей конфигурации сети."
        )
    else:
        explanation = (
            f"Лот рассматривается как объект обслуживания с аукционом на повышение тарифа. "
            f"Предельный сервисный тариф {break_even:.2f}, рекомендуемый потолок {recommended:.2f}. "
            f"Ожидаемый delta-profit {expected_delta:.2f}."
        )
    mounting_requirements = [issue.message for issue in topology_report.issues if issue.severity == "critical"]
    conflicts = [issue.message for issue in topology_report.issues if issue.severity == "warning"]
    synergy_notes = list(enabler_notes)
    if expected_delta <= 0.0:
        conflicts.append("Текущая конфигурация не даёт положительного ожидаемого delta-profit.")
    return LotEvaluation(
        lot_id=int(lot_id),
        lot_name=lot_name,
        auction_direction=direction,
        lot_profile=lot_profile,
        expected_delta_profit=expected_delta,
        break_even_tariff=round(break_even, 4),
        recommended_bid_or_tariff=round(recommended, 4),
        best_case=scenario_reports["best"],
        base_case=scenario_reports["base"],
        worst_case=scenario_reports["worst"],
        topology_risk=_risk_band(topology_score),
        market_risk=_risk_band(market_spread),
        balancing_risk=_risk_band(balancing_score),
        loss_risk=_risk_band(loss_score),
        explanation=explanation,
        direct_delta_profit=direct_delta,
        enabler_value=enabler_value,
        bundle_synergy_value=bundle_synergy,
        maintenance_tariff_total=_candidate_tariff_total(candidate, direction),
        topology=topology_report,
        storage_value=deepcopy(direct["base_result"].totals.storage),
        synergy_notes=synergy_notes,
        mounting_requirements=mounting_requirements,
        conflicts=conflicts,
        assumptions=assumptions,
        minimum_acceptable_tariff=round(break_even, 4) if direction == "descending_consumer_tariff" else None,
        recommended_walkdown_tariff=round(recommended, 4) if direction == "descending_consumer_tariff" else None,
        aggressive_floor=round(float(aggressive_floor or break_even), 4)
        if direction == "descending_consumer_tariff"
        else None,
        hard_floor=round(break_even, 4) if direction == "descending_consumer_tariff" else None,
        maximum_acceptable_service_tariff=round(break_even, 4) if direction != "descending_consumer_tariff" else None,
        recommended_bid_ceiling=round(recommended, 4) if direction != "descending_consumer_tariff" else None,
        soft_ceiling=round(max(0.0, recommended * 0.92), 4) if direction != "descending_consumer_tariff" else None,
        hard_ceiling=round(break_even, 4) if direction != "descending_consumer_tariff" else None,
        recommended_opening_bid=round(opening_bid, 4),
        recommended_counter_bid=round(counter_bid, 4),
        hard_limit=round(hard_limit, 4),
        allpay_trigger_policy=allpay_policy,
        drop_candidate_score=round(max(0.0, bundle_synergy + enabler_value - max(0.0, expected_delta) * 0.15), 4),
        portfolio_substitute_group=lot_profile,
        plan_b_if_lost="Переключиться на следующий лот с тем же профилем и меньшим topology risk.",
        plan_c_if_overbid="Снизить участие до recommended_counter_bid и сохранить бюджет под второй круг.",
    )


def simulate_system(
    *,
    objects: Sequence[EnergyObject],
    forecast_pack: Dict[str, Dict[str, Dict[int, float]]] | None,
    ruleset_config: Dict[str, Any] | None,
    scenario_label: str = "base",
    declared_sales: Sequence[float] | None = None,
) -> SimulationResult:
    config = ies2026_config(ruleset_config)
    dataset = dataset_from_pack(forecast_pack, config=config)
    working = _clone_objects(objects)
    return _simulate(
        objects=working,
        dataset=dataset,
        config=config,
        scenario_label=scenario_label,
        declared_sales=declared_sales,
    )
