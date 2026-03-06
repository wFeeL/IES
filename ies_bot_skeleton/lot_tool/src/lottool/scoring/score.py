from __future__ import annotations

from typing import Dict, List, Tuple

from lottool.io.forecast_loader import ForecastPack, lookup
from lottool.model.types import Breakdown, ObjectItem, State
from lottool.network.approx import approx_network_cost
from lottool.network.validators import validate_network_plan


def _is_consumer(kind: str) -> bool:
    return kind in ("houseA", "houseB", "office", "factory")


def _is_gen(kind: str) -> bool:
    return kind in ("wind", "solarRobot", "tps")


def _is_storage(kind: str) -> bool:
    return kind == "storage"


def _consumer_group(kind: str) -> str:
    return "factory" if kind == "factory" else "class3"


def _qty(item: ObjectItem) -> int:
    try:
        return max(1, int(item.qty or 1))
    except Exception:
        return 1


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wind_power_mw(w: float, obj_defaults: Dict) -> float:
    if w > 100.0:  # already MW
        return w
    k = float(obj_defaults.get("wind_k_default", 0.04))
    cap = float(obj_defaults.get("wind_cap_mw", 20.0))
    p = k * (w**3)
    return max(0.0, min(cap, p))


def _solar_power_mw(s: float, obj_defaults: Dict) -> float:
    cap = float(obj_defaults.get("solar_cap_mw", 25.0))
    if s <= 1.5:
        return max(0.0, min(cap, s * cap))
    return max(0.0, min(cap, s))


def _scenario_multipliers(state: State, cfg: Dict, scenario: str) -> Dict[str, float]:
    scen_cfg = cfg.get("scenarios", {}) or {}
    one = {"wind": 1.0, "solar": 1.0, "load": 1.0}
    if isinstance(scen_cfg, dict) and scenario in scen_cfg and isinstance(scen_cfg[scenario], dict):
        item = scen_cfg[scenario]
        return {
            "wind": float(item.get("wind", one["wind"])),
            "solar": float(item.get("solar", one["solar"])),
            "load": float(item.get("load", one["load"])),
        }

    corridor = dict(state.assumptions.corridor or {})
    wind_mul = float(corridor.get("wind_mul", 0.10))
    solar_mul = float(corridor.get("solar_mul", 0.10))
    load_mul = float(corridor.get("load_mul", 0.10))
    if scenario == "base":
        return one
    if scenario == "worst":
        return {"wind": 1.0 - wind_mul, "solar": 1.0 - solar_mul, "load": 1.0 + load_mul}
    if scenario == "best":
        return {"wind": 1.0 + wind_mul, "solar": 1.0 + solar_mul, "load": 1.0 - load_mul}
    return one


def _storage_soc_init(state: State, cap_total: float) -> float:
    if cap_total <= 0:
        return 0.0

    fraction_raw = getattr(state.assumptions, "storage_soc_init_fraction", None)
    absolute_raw = getattr(state.assumptions, "storage_soc_init", None)

    if absolute_raw is not None:
        return _clamp(_as_float(absolute_raw, cap_total * 0.5), 0.0, cap_total)

    if fraction_raw is not None:
        fraction = _clamp(_as_float(fraction_raw, 0.5), 0.0, 1.0)
        return cap_total * fraction

    # Default documented behavior for storage when explicit assumptions are absent.
    return cap_total * 0.5


def _sum_qty(items: List[ObjectItem], kind: str) -> int:
    total = 0
    for item in items:
        if item.kind == kind:
            total += _qty(item)
    return total


def score_state(
    state: State,
    objects: List[ObjectItem],
    forecasts: ForecastPack,
    cfg: Dict,
    scenario: str,
) -> Breakdown:
    horizon = int(state.game.horizon_ticks or state.game.ticks_per_day or 48)

    scales = _scenario_multipliers(state, cfg, scenario)
    mw_wind = float(scales["wind"])
    mw_solar = float(scales["solar"])
    mw_load = float(scales["load"])

    eco = cfg.get("eco", {})
    net_cfg = cfg.get("network", {})
    fine_cfg = cfg.get("fine", {})
    market_cfg = cfg.get("market", {})
    tps_cfg = cfg.get("tps", {})
    storage_cfg = cfg.get("storage", {})
    obj_def = cfg.get("object_defaults", {})

    ext_buy = float(market_cfg.get("external_buy_price", 10.0))
    ext_sell = float(market_cfg.get("external_sell_price", 1.0))
    instant_buy = float(market_cfg.get("instant_buy_price", max(ext_buy, 20.0)))
    instant_sell = float(market_cfg.get("instant_sell_price", min(ext_sell, 0.0)))
    market_limit = float(market_cfg.get("market_max_power", market_cfg.get("max_power_mw", 60.0)))
    instant_buy_limit = float(
        market_cfg.get("instant_buy_max_power", market_cfg.get("instant_max_power_mw", 1e9))
    )
    instant_sell_limit = float(
        market_cfg.get("instant_sell_max_power", market_cfg.get("instant_max_power_mw", 1e9))
    )

    fuel_max = float(tps_cfg.get("fuel_max", 20.0))
    eta_nom = float(tps_cfg.get("eta_nominal", 0.40))
    fuel_price = float(tps_cfg.get("fuel_price", 0.5))
    eco_tax_fuel = float(tps_cfg.get("eco_tax_fuel", 1.5))

    storage_cap = float(storage_cfg.get("capacity_mw_tick", 80.0))
    storage_ch_rate = float(storage_cfg.get("charge_rate_mw", 15.0))
    storage_dis_rate = float(storage_cfg.get("discharge_rate_mw", 20.0))
    storage_leak = float(storage_cfg.get("leak_fraction_per_tick", 0.0))

    wind_pts = float(eco.get("wind_points_per_mw_tick", 2.0))
    solar_pts = float(eco.get("solar_points_per_mw_tick", 3.0))
    stor_pts = float(eco.get("storage_discharge_points_per_mw_tick", 1.0))
    eco_point_value = float(eco.get("eco_point_value_rub", 0.0))

    class3_pardon = float(fine_cfg.get("class3_pardon", 5.0))
    class3_fine = float(fine_cfg.get("class3_rub_per_mw", 36.0))
    factory_fine = float(fine_cfg.get("factory_rub_per_mw", 57.0))

    wear_threshold = float(net_cfg.get("wear_threshold_mw", net_cfg.get("wear_overload_mw", 40.0)))
    wear_limit = float(net_cfg.get("wear_limit", 14.0))
    wear_k = float(net_cfg.get("wear_k", 0.20))
    outage_ticks = max(1, int(net_cfg.get("outage_ticks", 1)))
    outage_penalty_rub = float(net_cfg.get("outage_penalty_rub", 0.0))
    outage_loss_scale = _clamp(float(net_cfg.get("outage_loss_scale", 1.0)), 0.0, 1.0)

    consumers = [o for o in objects if _is_consumer(o.kind)]
    gens = [o for o in objects if _is_gen(o.kind)]
    storages = [o for o in objects if _is_storage(o.kind)]

    storage_qty_total = sum(_qty(s) for s in storages)
    storage_cap_total = storage_cap * storage_qty_total
    storage_ch_rate_total = storage_ch_rate * storage_qty_total
    storage_dis_rate_total = storage_dis_rate * storage_qty_total

    income = 0.0
    penalties = 0.0
    contracts = sum(float(o.contract_rub_per_tick or 0.0) * _qty(o) for o in objects) * horizon
    fuel_and_taxes = 0.0
    market_net = 0.0
    eco_points = 0.0
    network_losses_cost = 0.0
    risk_penalty = 0.0
    notes: List[str] = []
    instant_buy_total = 0.0
    instant_sell_total = 0.0

    ok_plan, issues = validate_network_plan(state.network_plan)
    if not ok_plan:
        notes.extend([f"NETPLAN:{x}" for x in issues])

    soc = _storage_soc_init(state, storage_cap_total)

    wear_state: Dict[str, float] = {branch.name: 0.0 for branch in state.network_plan.branches}
    outage_left: Dict[str, int] = {branch.name: 0 for branch in state.network_plan.branches}

    for t in range(horizon):
        offline_branches = {name for name, ticks in outage_left.items() if ticks > 0}
        for name in list(outage_left.keys()):
            if outage_left[name] > 0:
                outage_left[name] -= 1

        offline_gen_objects = set()
        offline_load_objects = set()
        for branch in state.network_plan.branches:
            if branch.name not in offline_branches:
                continue
            if branch.role == "gen":
                offline_gen_objects.update(str(x) for x in branch.objects)
            elif branch.role == "load":
                offline_load_objects.update(str(x) for x in branch.objects)

        load_by_id: Dict[str, float] = {}
        load_by_group = {"class3": 0.0, "factory": 0.0}
        unservable_by_group = {"class3": 0.0, "factory": 0.0}
        consumer_tick_rows: List[Tuple[ObjectItem, float, float]] = []
        total_load = 0.0
        unservable_load = 0.0

        for c in consumers:
            base = lookup(
                forecasts, "load", (c.id, c.kind, _consumer_group(c.kind)), t, default=0.0
            )
            demand = max(0.0, base * mw_load) * _qty(c)
            group = _consumer_group(c.kind)
            load_by_id[c.id] = load_by_id.get(c.id, 0.0) + demand
            load_by_group[group] += demand
            total_load += demand

            forced_unservable = 0.0
            if c.id in offline_load_objects:
                forced_unservable = min(demand, demand * outage_loss_scale)
                unservable_by_group[group] += forced_unservable
                unservable_load += forced_unservable

            consumer_tick_rows.append((c, demand, forced_unservable))

        gen_wind = 0.0
        gen_solar = 0.0
        gen_tps = 0.0
        lost_generation = 0.0
        gen_by_id: Dict[str, float] = {}

        for g in gens:
            qty = _qty(g)
            if g.kind == "wind":
                w = lookup(forecasts, "wind", (g.id, "wind"), t, default=0.0)
                p = _wind_power_mw(w * mw_wind, obj_def) * qty
                if g.id in offline_gen_objects:
                    lost = min(p, p * outage_loss_scale)
                    p -= lost
                    lost_generation += lost
                gen_wind += p
                gen_by_id[g.id] = gen_by_id.get(g.id, 0.0) + p
            elif g.kind == "solarRobot":
                s = lookup(forecasts, "solar", (g.id, "solar", "solarRobot"), t, default=0.0)
                p = _solar_power_mw(s * mw_solar, obj_def) * qty
                if g.id in offline_gen_objects:
                    lost = min(p, p * outage_loss_scale)
                    p -= lost
                    lost_generation += lost
                gen_solar += p
                gen_by_id[g.id] = gen_by_id.get(g.id, 0.0) + p
            elif g.kind == "tps":
                gen_by_id[g.id] = gen_by_id.get(g.id, 0.0)

        if storage_leak > 0 and soc > 0:
            soc = max(0.0, soc * (1.0 - storage_leak))

        supply = gen_wind + gen_solar

        if ok_plan and state.network_plan.branches:
            branch_flows = {}
            for b in state.network_plan.branches:
                gsum = 0.0
                lsum = 0.0
                for oid in b.objects:
                    obj_id = str(oid)
                    gsum += gen_by_id.get(obj_id, 0.0)
                    lsum += load_by_id.get(obj_id, 0.0)
                flow = max(gsum, lsum)
                if b.name in offline_branches:
                    flow = 0.0
                branch_flows[b.name] = flow

            res = approx_network_cost(state.network_plan, branch_flows, net_cfg)
            notes.extend(res.flags)
            loss_mw = res.loss_mw_tick
            network_losses_cost += res.loss_cost_rub
            risk_penalty += res.risk_penalty_rub

            for b in state.network_plan.branches:
                if b.name in offline_branches:
                    continue
                flow = float(branch_flows.get(b.name, 0.0))
                if flow > wear_threshold:
                    wear_state[b.name] += wear_k * (flow - wear_threshold)
                if wear_state[b.name] > wear_limit:
                    outage_left[b.name] = max(outage_left.get(b.name, 0), outage_ticks)
                    wear_state[b.name] = 0.0
                    risk_penalty += outage_penalty_rub
                    notes.append(f"WEAR_OUTAGE:{b.name} ticks={outage_ticks}")
        else:
            loss_mw = 0.02 * max(supply, total_load)
            network_losses_cost += loss_mw * float(net_cfg.get("loss_tax", 2.0))

        supply_eff = max(0.0, supply - loss_mw)
        serviceable_load = max(0.0, total_load - unservable_load)

        discharge = 0.0
        deficit = max(0.0, serviceable_load - supply_eff)
        if storage_qty_total > 0 and deficit > 0 and soc > 0:
            discharge = min(storage_dis_rate_total, soc, deficit)
            soc -= discharge
            supply_eff += discharge
            deficit = max(0.0, serviceable_load - supply_eff)

        tps_cap = _sum_qty(gens, "tps") * (fuel_max * eta_nom)
        if deficit > 0 and tps_cap > 0:
            tps_gen = min(deficit, tps_cap)
            supply_eff += tps_gen
            gen_tps += tps_gen
            deficit -= tps_gen
            fuel_used = tps_gen / max(0.05, eta_nom)
            fuel_and_taxes += fuel_used * (fuel_price + eco_tax_fuel)

        if deficit > 0:
            normal = min(deficit, market_limit)
            if normal > 0:
                market_net += normal * ext_buy
                supply_eff += normal
                deficit -= normal

            inst = min(deficit, instant_buy_limit)
            if inst > 0:
                market_net += inst * instant_buy
                supply_eff += inst
                deficit -= inst
                instant_buy_total += inst

        surplus = max(0.0, supply_eff - serviceable_load)
        if storage_qty_total > 0 and surplus > 0 and soc < storage_cap_total:
            ch = min(storage_ch_rate_total, storage_cap_total - soc, surplus)
            soc += ch
            supply_eff -= ch
            surplus = max(0.0, supply_eff - serviceable_load)

        if surplus > 0:
            normal_sell = min(surplus, market_limit)
            if normal_sell > 0:
                market_net -= normal_sell * ext_sell
                surplus -= normal_sell

            inst_sell = min(surplus, instant_sell_limit)
            if inst_sell > 0:
                market_net -= inst_sell * instant_sell
                surplus -= inst_sell
                instant_sell_total += inst_sell

        unmet_service = max(0.0, serviceable_load - supply_eff)
        unmet_total_effective = unservable_load + unmet_service
        unmet_service_ratio = (
            unmet_service / max(1e-9, serviceable_load) if serviceable_load > 1e-9 else 0.0
        )

        if unmet_total_effective > 1e-9:
            class3_serviceable = max(0.0, load_by_group["class3"] - unservable_by_group["class3"])
            factory_serviceable = max(
                0.0, load_by_group["factory"] - unservable_by_group["factory"]
            )
            class3_unserved = (
                unservable_by_group["class3"] + class3_serviceable * unmet_service_ratio
            )
            factory_unserved = (
                unservable_by_group["factory"] + factory_serviceable * unmet_service_ratio
            )
            pen = (
                max(0.0, class3_unserved - class3_pardon) * class3_fine
                + factory_unserved * factory_fine
            )
            penalties += pen

        for consumer, demand, forced_unservable in consumer_tick_rows:
            serviceable = max(0.0, demand - forced_unservable)
            unmet_for_consumer = serviceable * unmet_service_ratio
            served = max(0.0, serviceable - unmet_for_consumer)
            income += float(consumer.tariff_rub_per_mw_tick or 0.0) * served

        eco_points += wind_pts * gen_wind + solar_pts * gen_solar + stor_pts * discharge

        if gen_tps > 0:
            notes.append(f"TPS_USED:t={t},mw={gen_tps:.2f}")
        if unservable_load > 0:
            notes.append(f"UNSERVABLE_LOAD:t={t},mw={unservable_load:.2f}")
        if lost_generation > 0:
            notes.append(f"LOST_GENERATION:t={t},mw={lost_generation:.2f}")

    eco_value = eco_points * eco_point_value
    score_total = (
        income
        - penalties
        - contracts
        - fuel_and_taxes
        - market_net
        - network_losses_cost
        - risk_penalty
        + eco_value
    )

    if instant_buy_total > 0:
        notes.append(f"INSTANT_BUY:mw={instant_buy_total:.2f},price={instant_buy:.2f}")
    if instant_sell_total > 0:
        notes.append(f"INSTANT_SELL:mw={instant_sell_total:.2f},price={instant_sell:.2f}")

    return Breakdown(
        score_total=score_total,
        income=income,
        penalties=penalties,
        contracts=contracts,
        fuel_and_taxes=fuel_and_taxes,
        market_net=market_net,
        network_losses_cost=network_losses_cost,
        eco_value=eco_value,
        risk_penalty=risk_penalty,
        eco_points=eco_points,
        notes=notes,
    )


__all__ = ["score_state"]
