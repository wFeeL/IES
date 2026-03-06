from __future__ import annotations

import argparse
import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

TICKS = 48
TEAM_CAPITAL = 200.0

TARIFF_RUB_PER_MW = {"house": 3.0, "plant": 4.0, "office": 5.0}

GRID_BUY_RUB_PER_MW = 10.0
GRID_SELL_RUB_PER_MW = 2.0
UNSERVED_PENALTY_RUB_PER_MW = 10.0

SUBSTATION_CAP_MW = 40.0

SOLAR_MAX_MW = 10.0
WIND_MAX_MW = 8.0
TPP_MAX_MW = 15.0

SOLAR_OPEX_RUB_PER_TICK = 2.0
WIND_OPEX_RUB_PER_TICK = 1.0
STORAGE_OPEX_RUB_PER_TICK = 3.0
SUBSTATION_OPEX_RUB_PER_TICK = 1.0
MINISUB_OPEX_RUB_PER_TICK = 0.0

VIE_BONUS_RUB_PER_MW = 2.0
TPP_FUEL_RUB_PER_MW = 0.5

STORAGE_CAP_MW_TICK = 20.0
STORAGE_RATE_MW = 5.0
STORAGE_LEAK_FRACTION = 0.05
STORAGE_INITIAL_SOC_FRACTION = 0.5

SUN_CORRIDOR = 0.5
LOAD_CORRIDOR = 0.5


@dataclass(frozen=True)
class System:
    solar: int = 0
    wind: int = 0
    tpp: int = 0
    storage: int = 0
    house: int = 0
    office: int = 0
    plant: int = 0
    substation: int = 0
    minisub: int = 0

    def add(self, other: "System") -> "System":
        return System(
            solar=self.solar + other.solar,
            wind=self.wind + other.wind,
            tpp=self.tpp + other.tpp,
            storage=self.storage + other.storage,
            house=self.house + other.house,
            office=self.office + other.office,
            plant=self.plant + other.plant,
            substation=self.substation + other.substation,
            minisub=self.minisub + other.minisub,
        )


@dataclass(frozen=True)
class LotEvaluation:
    base_profit: float
    with_lot_profit: float
    deterministic_value: float
    expected_value: float
    std: float
    q10: float
    q50: float
    q90: float
    min_bid: float
    max_bid: float


@dataclass(frozen=True)
class LotStats:
    name: str
    start_price: float
    mean_delta: float
    stdev_delta: float
    q10: float
    q50: float
    q90: float
    value_cap: float


START_SYSTEM = System(solar=1, house=1, minisub=1, substation=1)

LOTS: Dict[str, Dict[str, Any]] = {
    "Lot1_TPP+Office": {"system": System(tpp=1, office=1), "start_price": 14.0},
    "Lot2_Wind+Plant": {"system": System(wind=1, plant=1), "start_price": 12.0},
    "Lot3_Solar+Storage": {"system": System(solar=1, storage=1), "start_price": 13.0},
    "Lot4_Minisub+Office": {"system": System(minisub=1, office=1), "start_price": 10.0},
    "Lot5_Minisub+Storage": {"system": System(minisub=1, storage=1), "start_price": 11.0},
}


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    sorted_vals = sorted(values)
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def plan_bids_all_pay(
    lots: List[LotStats],
    budget: float = TEAM_CAPITAL,
    reserve: float = 30.0,
    alpha: float = 0.35,
    min_bid: float = 0.0,
    require_positive_q10: bool = False,
) -> Dict[str, float]:
    spendable = max(0.0, budget - reserve)

    candidates: List[LotStats] = []
    for s in lots:
        if s.value_cap <= 0.0:
            continue
        if require_positive_q10 and s.q10 <= 0.0:
            continue
        candidates.append(s)

    if not candidates or spendable <= 0.0:
        return {s.name: 0.0 for s in lots}

    raw: Dict[str, float] = {}
    for s in candidates:
        b = alpha * s.value_cap
        b = max(min_bid, min(b, s.value_cap))
        raw[s.name] = b

    total = sum(raw.values())
    if total <= 1e-9:
        return {s.name: 0.0 for s in lots}

    k = min(1.0, spendable / total)
    bids = {name: round(b * k, 2) for name, b in raw.items()}
    rounded_total = sum(bids.values())
    if rounded_total > spendable + 1e-9:
        overflow = round(rounded_total - spendable, 2)
        if overflow > 0:
            biggest = max((n for n in bids if bids[n] > 0.0), key=lambda n: bids[n], default=None)
            if biggest is not None:
                bids[biggest] = round(max(0.0, bids[biggest] - overflow), 2)
    for s in lots:
        bids.setdefault(s.name, 0.0)
    return bids


def _pick_col(columns: List[str], aliases: Iterable[str]) -> str:
    lc = [c.lower() for c in columns]
    for alias in aliases:
        a = alias.lower()
        for i, col in enumerate(lc):
            if a in col:
                return columns[i]
    raise ValueError(f"Cannot detect column by aliases={list(aliases)} in {columns}")


def _normalize_system_like(value: Any) -> System:
    if isinstance(value, System):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("System/lot must be System or dict-like")

    aliases = {
        "solar": "solar",
        "sun": "solar",
        "wind": "wind",
        "tpp": "tpp",
        "tps": "tpp",
        "tes": "tpp",
        "storage": "storage",
        "battery": "storage",
        "house": "house",
        "home": "house",
        "office": "office",
        "plant": "plant",
        "factory": "plant",
        "substation": "substation",
        "main_substation": "substation",
        "mini_substation": "minisub",
        "mini-substation": "minisub",
        "minisub": "minisub",
    }
    accum: Dict[str, int] = {
        "solar": 0,
        "wind": 0,
        "tpp": 0,
        "storage": 0,
        "house": 0,
        "office": 0,
        "plant": 0,
        "substation": 0,
        "minisub": 0,
    }

    src: Any = value
    if "items" in value and isinstance(value["items"], Mapping):
        src = value["items"]
    if "items" in value and isinstance(value["items"], list):
        for item in value["items"]:
            if not isinstance(item, Mapping):
                continue
            key = aliases.get(str(item.get("type", item.get("kind", ""))).lower())
            if key is None:
                continue
            count = int(_as_float(item.get("count", item.get("qty", 1)), 1))
            accum[key] += max(0, count)
    elif isinstance(src, Mapping):
        for raw_key, raw_val in src.items():
            key = aliases.get(str(raw_key).lower().replace(" ", "_"))
            if key is None:
                continue
            count = int(_as_float(raw_val, 0.0))
            accum[key] += max(0, count)

    return System(
        solar=accum["solar"],
        wind=accum["wind"],
        tpp=accum["tpp"],
        storage=accum["storage"],
        house=accum["house"],
        office=accum["office"],
        plant=accum["plant"],
        substation=accum["substation"],
        minisub=accum["minisub"],
    )

def load_forecast_csv(path: str | Path) -> List[Dict[str, float]]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        c_sun = _pick_col(columns, ["sun", "solar", "солн"])
        c_wind = _pick_col(columns, ["wind", "вет"])
        c_house = _pick_col(columns, ["house", "home", "дом"])
        c_office = _pick_col(columns, ["office", "офис"])
        c_plant = _pick_col(columns, ["plant", "factory", "завод"])

        rows: List[Dict[str, float]] = []
        for row in reader:
            rows.append(
                {
                    "sun": max(0.0, _as_float(row.get(c_sun), 0.0)),
                    "wind": max(0.0, _as_float(row.get(c_wind), 0.0)),
                    "house": max(0.0, _as_float(row.get(c_house), 0.0)),
                    "office": max(0.0, _as_float(row.get(c_office), 0.0)),
                    "plant": max(0.0, _as_float(row.get(c_plant), 0.0)),
                }
            )
    if len(rows) < TICKS:
        raise ValueError(f"Forecast has {len(rows)} rows, expected at least {TICKS}")
    return rows[:TICKS]


def sample_forecast(rows: List[Dict[str, float]], rng: random.Random) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    for row in rows:
        out.append(
            {
                "sun": max(0.0, row["sun"] * rng.uniform(1.0 - SUN_CORRIDOR, 1.0 + SUN_CORRIDOR)),
                "wind": row["wind"],
                "house": max(
                    0.0, row["house"] * rng.uniform(1.0 - LOAD_CORRIDOR, 1.0 + LOAD_CORRIDOR)
                ),
                "office": max(
                    0.0, row["office"] * rng.uniform(1.0 - LOAD_CORRIDOR, 1.0 + LOAD_CORRIDOR)
                ),
                "plant": max(
                    0.0, row["plant"] * rng.uniform(1.0 - LOAD_CORRIDOR, 1.0 + LOAD_CORRIDOR)
                ),
            }
        )
    return out


def _serve_by_priority(demand: Dict[str, float], cap_mw: float) -> Dict[str, float]:
    served = {"office": 0.0, "plant": 0.0, "house": 0.0}
    left = cap_mw
    for name in ("office", "plant", "house"):
        mw = min(demand[name], left)
        served[name] = mw
        left -= mw
        if left <= 1e-9:
            break
    return served


def simulate_profit(system: System, rows: List[Dict[str, float]], allow_tpp_export: bool = True) -> float:
    fixed_opex = (
        system.substation * SUBSTATION_OPEX_RUB_PER_TICK
        + system.minisub * MINISUB_OPEX_RUB_PER_TICK
        + system.solar * SOLAR_OPEX_RUB_PER_TICK
        + system.wind * WIND_OPEX_RUB_PER_TICK
        + system.storage * STORAGE_OPEX_RUB_PER_TICK
    )

    cap_total = system.storage * STORAGE_CAP_MW_TICK
    rate_total = system.storage * STORAGE_RATE_MW
    soc = cap_total * STORAGE_INITIAL_SOC_FRACTION
    tpp_cap = system.tpp * TPP_MAX_MW

    total = 0.0

    for t in range(TICKS):
        row = rows[t]
        if soc > 0.0:
            soc *= 1.0 - STORAGE_LEAK_FRACTION

        demand = {
            "house": row["house"] * system.house,
            "office": row["office"] * system.office,
            "plant": row["plant"] * system.plant,
        }
        total_demand = demand["house"] + demand["office"] + demand["plant"]

        served = _serve_by_priority(demand, SUBSTATION_CAP_MW)
        served_total = served["house"] + served["office"] + served["plant"]
        unserved = max(0.0, total_demand - served_total)

        solar_mw = min(SOLAR_MAX_MW, row["sun"]) * system.solar
        wind_mw = min(WIND_MAX_MW, row["wind"]) * system.wind
        renew = solar_mw + wind_mw

        used_renew_for_served = min(renew, served_total)
        renew_surplus = max(0.0, renew - used_renew_for_served)
        need_after_renew = max(0.0, served_total - used_renew_for_served)

        discharge = min(rate_total, soc, need_after_renew) if rate_total > 0 else 0.0
        soc -= discharge
        need_after_discharge = max(0.0, need_after_renew - discharge)

        tpp_for_served = min(tpp_cap, need_after_discharge)
        need_after_tpp = max(0.0, need_after_discharge - tpp_for_served)
        grid_buy = need_after_tpp

        throughput_left = max(0.0, SUBSTATION_CAP_MW - served_total)

        charge = 0.0
        if rate_total > 0 and cap_total > soc and renew_surplus > 0 and throughput_left > 0:
            charge = min(rate_total, cap_total - soc, renew_surplus, throughput_left)
            soc += charge
            renew_surplus -= charge
            throughput_left -= charge

        sell_renew = min(renew_surplus, throughput_left)
        throughput_left -= sell_renew

        tpp_for_sell = 0.0
        if allow_tpp_export and throughput_left > 0 and tpp_cap > tpp_for_served:
            tpp_for_sell = min(tpp_cap - tpp_for_served, throughput_left)
            throughput_left -= tpp_for_sell

        tpp_total = tpp_for_served + tpp_for_sell
        sell_total = sell_renew + tpp_for_sell

        served_revenue = (
            served["house"] * TARIFF_RUB_PER_MW["house"]
            + served["plant"] * TARIFF_RUB_PER_MW["plant"]
            + served["office"] * TARIFF_RUB_PER_MW["office"]
        )

        renewable_used_total = used_renew_for_served + charge + sell_renew
        vie_bonus = renewable_used_total * VIE_BONUS_RUB_PER_MW

        tick_profit = (
            served_revenue
            + vie_bonus
            + sell_total * GRID_SELL_RUB_PER_MW
            - grid_buy * GRID_BUY_RUB_PER_MW
            - tpp_total * TPP_FUEL_RUB_PER_MW
            - unserved * UNSERVED_PENALTY_RUB_PER_MW
            - fixed_opex
        )
        total += tick_profit

    return total


def evaluate_lot(
    current_system: System | Mapping[str, Any],
    lot: System | Mapping[str, Any],
    forecast_rows: List[Dict[str, float]],
    *,
    start_price: float = 0.0,
    n_sims: int = 1000,
    seed: int = 42,
    allow_tpp_export: bool = False,
) -> LotEvaluation:
    base_system = _normalize_system_like(current_system)
    lot_system = _normalize_system_like(lot)
    full_system = base_system.add(lot_system)

    base_profit = simulate_profit(base_system, forecast_rows, allow_tpp_export=allow_tpp_export)
    with_lot_profit = simulate_profit(full_system, forecast_rows, allow_tpp_export=allow_tpp_export)
    deterministic = with_lot_profit - base_profit

    rng = random.Random(seed)
    deltas: List[float] = []
    sims = max(1, int(n_sims))
    for _ in range(sims):
        sampled = sample_forecast(forecast_rows, rng)
        b = simulate_profit(base_system, sampled, allow_tpp_export=allow_tpp_export)
        w = simulate_profit(full_system, sampled, allow_tpp_export=allow_tpp_export)
        deltas.append(w - b)

    expected = statistics.fmean(deltas)
    std = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    q10 = _quantile(deltas, 0.10)
    q50 = _quantile(deltas, 0.50)
    q90 = _quantile(deltas, 0.90)

    max_bid = max(0.0, min(TEAM_CAPITAL, expected))
    min_bid = start_price if 0.0 < start_price <= max_bid else 0.0

    return LotEvaluation(
        base_profit=base_profit,
        with_lot_profit=with_lot_profit,
        deterministic_value=deterministic,
        expected_value=expected,
        std=std,
        q10=q10,
        q50=q50,
        q90=q90,
        min_bid=min_bid,
        max_bid=max_bid,
    )


def analyze_all_lots(
    forecast_rows: List[Dict[str, float]],
    *,
    n_sims: int,
    seed: int,
    allow_tpp_export: bool,
    reserve: float,
    alpha: float,
    risk_aversion: float,
    require_positive_q10: bool,
) -> None:
    evaluations: Dict[str, LotEvaluation] = {}
    stats: List[LotStats] = []

    for name, lot_data in LOTS.items():
        res = evaluate_lot(
            START_SYSTEM,
            lot_data["system"],
            forecast_rows,
            start_price=float(lot_data["start_price"]),
            n_sims=n_sims,
            seed=seed,
            allow_tpp_export=allow_tpp_export,
        )
        evaluations[name] = res
        value_cap = max(0.0, min(res.max_bid, res.expected_value - risk_aversion * res.std))
        stats.append(
            LotStats(
                name=name,
                start_price=float(lot_data["start_price"]),
                mean_delta=res.expected_value,
                stdev_delta=res.std,
                q10=res.q10,
                q50=res.q50,
                q90=res.q90,
                value_cap=value_cap,
            )
        )

    recommended = plan_bids_all_pay(
        lots=stats,
        budget=TEAM_CAPITAL,
        reserve=reserve,
        alpha=alpha,
        min_bid=0.0,
        require_positive_q10=require_positive_q10,
    )

    for s in stats:
        if 0.0 < recommended[s.name] < s.start_price:
            recommended[s.name] = 0.0

    total_rec = sum(recommended.values())
    spendable = max(0.0, TEAM_CAPITAL - reserve)

    print(f"Horizon: {TICKS} ticks | Team capital: {TEAM_CAPITAL:.0f} RUB")
    print(
        f"All-pay strategy: reserve={reserve:.2f}, spendable={spendable:.2f}, "
        f"risk_aversion={risk_aversion:.2f}, alpha={alpha:.2f}, "
        f"q10_filter={'on' if require_positive_q10 else 'off'}, "
        f"total_recommended={total_rec:.2f}"
    )
    print(
        "lot                         start   value_det   value_ev    q10        q50        q90"
        "      bid_min    bid_max    bid_rec"
    )
    print("-" * 132)
    for name, lot_data in LOTS.items():
        res = evaluations[name]
        print(
            f"{name:<26}  "
            f"{lot_data['start_price']:>6.2f}  "
            f"{res.deterministic_value:>9.2f}  "
            f"{res.expected_value:>9.2f}  "
            f"{res.q10:>9.2f}  "
            f"{res.q50:>9.2f}  "
            f"{res.q90:>9.2f}  "
            f"{res.min_bid:>10.2f}  "
            f"{res.max_bid:>9.2f}  "
            f"{recommended[name]:>8.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="48-tick lot analyzer (single-file)")
    parser.add_argument(
        "--csv",
        default=str(Path(__file__).resolve().parent / "data" / "forecast.csv"),
        help="Path to forecast.csv",
    )
    parser.add_argument("--sims", type=int, default=1000, help="Monte-Carlo simulations count")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--allow-tpp-export",
        action="store_true",
        help="Allow selling extra TPP generation to external grid",
    )
    parser.add_argument(
        "--reserve",
        type=float,
        default=30.0,
        help="Reserved capital for tie-break rounds / uncertainty",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.35,
        help="How aggressively to convert value cap into bid",
    )
    parser.add_argument(
        "--risk-aversion",
        type=float,
        default=0.35,
        help="Risk adjustment: value_cap = mean - risk_aversion * std",
    )
    parser.add_argument(
        "--q10-filter",
        action="store_true",
        help="Bid only lots with positive q10 value",
    )
    args = parser.parse_args()

    rows = load_forecast_csv(args.csv)
    analyze_all_lots(
        rows,
        n_sims=max(1, args.sims),
        seed=args.seed,
        allow_tpp_export=args.allow_tpp_export,
        reserve=max(0.0, args.reserve),
        alpha=_clamp(args.alpha, 0.0, 1.0),
        risk_aversion=max(0.0, args.risk_aversion),
        require_positive_q10=args.q10_filter,
    )


if __name__ == "__main__":
    main()
