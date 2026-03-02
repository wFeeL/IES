from __future__ import annotations

from typing import Dict, List

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


def _expand(items: List[ObjectItem]) -> List[ObjectItem]:
    out: List[ObjectItem] = []
    for it in items:
        q = max(1, int(it.qty or 1))
        for i in range(q):
            if q == 1:
                out.append(it)
            else:
                out.append(ObjectItem(
                    kind=it.kind,
                    id=f"{it.id}#{i+1}",
                    qty=1,
                    contract_rub_per_tick=it.contract_rub_per_tick,
                    tariff_rub_per_mw_tick=it.tariff_rub_per_mw_tick,
                    meta=dict(it.meta or {}),
                ))
    return out


def _wind_power_mw(w: float, obj_defaults: Dict) -> float:
    if w > 100.0:  # already MW
        return w
    k = float(obj_defaults.get("wind_k_default", 0.04))
    cap = float(obj_defaults.get("wind_cap_mw", 20.0))
    p = k * (w ** 3)
    return max(0.0, min(cap, p))


def _solar_power_mw(s: float, obj_defaults: Dict) -> float:
    cap = float(obj_defaults.get("solar_cap_mw", 25.0))
    if s <= 1.5:
        return max(0.0, min(cap, s * cap))
    return max(0.0, min(cap, s))


def score_state(state: State, objects: List[ObjectItem], forecasts: ForecastPack, cfg: Dict, scenario: str) -> Breakdown:
    horizon = int(state.game.horizon_ticks or state.game.ticks_per_day or 48)

    corridor = dict(state.assumptions.corridor or {})
    wind_mul = float(corridor.get("wind_mul", 0.10))
    solar_mul = float(corridor.get("solar_mul", 0.10))
    load_mul = float(corridor.get("load_mul", 0.10))

    if scenario == "base":
        mw_wind, mw_solar, mw_load = 1.0, 1.0, 1.0
    elif scenario == "worst":
        mw_wind, mw_solar, mw_load = 1.0 - wind_mul, 1.0 - solar_mul, 1.0 + load_mul
    elif scenario == "best":
        mw_wind, mw_solar, mw_load = 1.0 + wind_mul, 1.0 + solar_mul, 1.0 - load_mul
    else:
        mw_wind, mw_solar, mw_load = 1.0, 1.0, 1.0

    eco = cfg.get("eco", {})
    net_cfg = cfg.get("network", {})
    market_cfg = cfg.get("market", {})
    tps_cfg = cfg.get("tps", {})
    storage_cfg = cfg.get("storage", {})
    obj_def = cfg.get("object_defaults", {})

    ext_buy = float(market_cfg.get("external_buy_price", 10.0))
    ext_sell = float(market_cfg.get("external_sell_price", 1.0))

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

    objs = _expand(objects)
    consumers = [o for o in objs if _is_consumer(o.kind)]
    gens = [o for o in objs if _is_gen(o.kind)]
    storages = [o for o in objs if _is_storage(o.kind)]

    income = 0.0
    penalties = 0.0
    contracts = sum(float(o.contract_rub_per_tick or 0.0) for o in objs) * horizon
    fuel_and_taxes = 0.0
    market_net = 0.0
    eco_points = 0.0
    network_losses_cost = 0.0
    risk_penalty = 0.0
    notes: List[str] = []

    ok_plan, issues = validate_network_plan(state.network_plan)
    if not ok_plan:
        notes.extend([f"NETPLAN:{x}" for x in issues])

    soc = storage_cap if storages else 0.0
    soc = max(0.0, min(storage_cap, soc))

    wind_ids = [g.id for g in gens if g.kind == "wind"]
    solar_ids = [g.id for g in gens if g.kind == "solarRobot"]

    for t in range(horizon):
        load_by_id = {}
        total_load = 0.0
        for c in consumers:
            base = lookup(forecasts, "load", (c.id, c.kind, _consumer_group(c.kind)), t, default=0.0)
            val = max(0.0, base * mw_load)
            load_by_id[c.id] = val
            total_load += val

        gen_wind = 0.0
        gen_solar = 0.0
        for g in gens:
            if g.kind == "wind":
                w = lookup(forecasts, "wind", (g.id, "wind"), t, default=0.0)
                gen_wind += _wind_power_mw(w * mw_wind, obj_def)
            elif g.kind == "solarRobot":
                s = lookup(forecasts, "solar", (g.id, "solar", "solarRobot"), t, default=0.0)
                gen_solar += _solar_power_mw(s * mw_solar, obj_def)

        if storage_leak > 0 and soc > 0:
            soc = max(0.0, soc * (1.0 - storage_leak))

        supply = gen_wind + gen_solar

        if ok_plan and state.network_plan.branches:
            branch_flows = {}
            for b in state.network_plan.branches:
                gsum = 0.0
                lsum = 0.0
                for oid in b.objects:
                    oid = str(oid)
                    if oid in wind_ids:
                        gsum += gen_wind / max(1, len(wind_ids))
                    if oid in solar_ids:
                        gsum += gen_solar / max(1, len(solar_ids))
                    if oid in load_by_id:
                        lsum += load_by_id[oid]
                branch_flows[b.name] = max(gsum, lsum)

            res = approx_network_cost(state.network_plan, branch_flows, net_cfg)
            notes.extend(res.flags)
            loss_mw = res.loss_mw_tick
            network_losses_cost += res.loss_cost_rub
            risk_penalty += res.risk_penalty_rub
        else:
            loss_mw = 0.02 * max(supply, total_load)
            network_losses_cost += loss_mw * float(net_cfg.get("loss_tax", 2.0))

        supply_eff = max(0.0, supply - loss_mw)

        discharge = 0.0
        deficit = max(0.0, total_load - supply_eff)
        if storages and deficit > 0 and soc > 0:
            discharge = min(storage_dis_rate, soc, deficit)
            soc -= discharge
            supply_eff += discharge
            deficit = max(0.0, total_load - supply_eff)

        tps_units = [g for g in gens if g.kind == "tps"]
        tps_cap = len(tps_units) * (fuel_max * eta_nom)
        if deficit > 0 and tps_cap > 0:
            tps_gen = min(deficit, tps_cap)
            supply_eff += tps_gen
            deficit -= tps_gen
            fuel_used = tps_gen / max(0.05, eta_nom)
            fuel_and_taxes += fuel_used * (fuel_price + eco_tax_fuel)

        if deficit > 0:
            market_net += deficit * ext_buy
            supply_eff += deficit

        surplus = max(0.0, supply_eff - total_load)
        if storages and surplus > 0 and soc < storage_cap:
            ch = min(storage_ch_rate, storage_cap - soc, surplus)
            soc += ch
            supply_eff -= ch
            surplus = max(0.0, supply_eff - total_load)

        if surplus > 0:
            market_net -= surplus * ext_sell

        for c in consumers:
            income += float(c.tariff_rub_per_mw_tick or 0.0) * load_by_id.get(c.id, 0.0)

        eco_points += wind_pts * gen_wind + solar_pts * gen_solar + stor_pts * discharge

    eco_value = eco_points * eco_point_value
    score_total = income - penalties - contracts - fuel_and_taxes - market_net - network_losses_cost - risk_penalty + eco_value

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
