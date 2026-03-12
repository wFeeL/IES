from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal

Kind = Literal[
    "main",
    "miniA",
    "miniB",
    "houseA",
    "houseB",
    "office",
    "factory",
    "wind",
    "solarRobot",
    "tps",
    "storage",
]

Role = Literal["gen", "load"]


@dataclass
class ObjectItem:
    kind: Kind
    id: str
    qty: int = 1
    contract_rub_per_tick: float = 0.0
    tariff_rub_per_mw_tick: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Lot:
    lot_id: str
    title: str = ""
    note: str = ""
    items: List[ObjectItem] = field(default_factory=list)
    suggested_bid: Optional[float] = None


@dataclass
class Branch:
    name: str
    role: Role
    objects: List[str] = field(default_factory=list)
    soft_flow_limit_mw: float = 30.0


@dataclass
class NetworkPlan:
    mode: str = "branches"
    branches: List[Branch] = field(default_factory=list)


@dataclass
class Budget:
    cash: float = 0.0
    allpay_spent: float = 0.0


@dataclass
class Game:
    ticks_per_day: int = 48
    horizon_ticks: int = 48


@dataclass
class Assumptions:
    pwin_default: float = 0.35
    risk_mode: str = "conservative"
    corridor: Dict[str, float] = field(
        default_factory=lambda: {"wind_mul": 0.10, "solar_mul": 0.10, "load_mul": 0.10}
    )
    storage_soc_init_fraction: Optional[float] = None
    storage_soc_init: Optional[float] = None


@dataclass
class State:
    schema_version: int = 1
    game: Game = field(default_factory=Game)
    budget: Budget = field(default_factory=Budget)
    owned_lots: List[str] = field(default_factory=list)
    owned_objects_override: List[ObjectItem] = field(default_factory=list)
    network_plan: NetworkPlan = field(default_factory=NetworkPlan)
    assumptions: Assumptions = field(default_factory=Assumptions)


@dataclass
class Breakdown:
    score_total: float
    income: float
    penalties: float
    contracts: float
    fuel_and_taxes: float
    market_net: float
    network_losses_cost: float
    eco_value: float
    risk_penalty: float
    eco_points: float
    notes: List[str] = field(default_factory=list)


@dataclass
class DeltaBreakdown:
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
    flags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
