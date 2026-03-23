from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ObjectCategory = Literal["consumer", "generator", "storage", "infrastructure"]
IssueSeverity = Literal["critical", "warning", "hint"]
AuctionDirection = Literal["descending_consumer_tariff", "ascending_service_tariff"]
RiskBand = Literal["low", "medium", "high"]


@dataclass(slots=True)
class ConnectionTerminal:
    key: str
    label: str
    required: bool
    parent_id: Optional[str] = None
    connection_point: str = "A"
    load_share: float = 1.0


@dataclass(slots=True)
class EnergyObject:
    object_id: str
    object_type_id: Optional[int]
    code: str
    name: str
    category: ObjectCategory
    district: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    source_lot_id: Optional[int] = None
    is_candidate: bool = False
    is_active: bool = True
    parent_id: Optional[str] = None
    terminals: List[ConnectionTerminal] = field(default_factory=list)


@dataclass(slots=True)
class ForecastTick:
    tick: int
    illumination: float
    market_buy_price: float
    market_sell_price: float
    balancing_penalty_price: float
    demand_by_type: Dict[str, float] = field(default_factory=dict)
    wind_channels: Dict[str, float] = field(default_factory=dict)
    extra_market: Dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class ForecastDataset:
    ticks: List[ForecastTick]
    wind_channels: List[str]
    assumptions: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarketBid:
    tick: int
    declared_mw: float
    actual_high_price_sale_mw: float
    low_price_sale_mw: float
    shortfall_mw: float
    high_price: float
    low_price: float
    balancing_penalty_price: float


@dataclass(slots=True)
class StorageValueBreakdown:
    arbitrage: float = 0.0
    balancing: float = 0.0
    reserve: float = 0.0


@dataclass(slots=True)
class SimulationTotals:
    consumer_revenue: float = 0.0
    market_revenue: float = 0.0
    market_purchase_cost: float = 0.0
    service_cost: float = 0.0
    loss_cost: float = 0.0
    balancing_penalty: float = 0.0
    unmet_load_penalty: float = 0.0
    flexibility_credit: float = 0.0
    reserve_credit: float = 0.0
    total_generation_mw: float = 0.0
    useful_generation_mw: float = 0.0
    served_load_mw: float = 0.0
    gross_load_mw: float = 0.0
    loss_mw: float = 0.0
    direct_profit: float = 0.0
    storage: StorageValueBreakdown = field(default_factory=StorageValueBreakdown)


@dataclass(slots=True)
class TopologyIssue:
    code: str
    message: str
    severity: IssueSeverity
    object_id: Optional[str] = None


@dataclass(slots=True)
class NetworkValidationReport:
    issues: List[TopologyIssue] = field(default_factory=list)
    recommended_connections: Dict[str, List[ConnectionTerminal]] = field(default_factory=dict)
    usable_fraction_by_object: Dict[str, float] = field(default_factory=dict)
    available_ports_by_node: Dict[str, int] = field(default_factory=dict)
    loss_fraction_by_object: Dict[str, float] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)


@dataclass(slots=True)
class SimulationResult:
    totals: SimulationTotals
    topology: NetworkValidationReport
    market_bids: List[MarketBid] = field(default_factory=list)
    deficit_ticks: List[int] = field(default_factory=list)
    surplus_ticks: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ScenarioReport:
    label: str
    delta_profit: float
    totals: SimulationTotals
    notes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class LotEvaluation:
    lot_id: int
    lot_name: str
    auction_direction: AuctionDirection
    expected_delta_profit: float
    break_even_tariff: float
    recommended_bid_or_tariff: float
    best_case: ScenarioReport
    base_case: ScenarioReport
    worst_case: ScenarioReport
    topology_risk: RiskBand
    market_risk: RiskBand
    balancing_risk: RiskBand
    loss_risk: RiskBand
    explanation: str
    direct_delta_profit: float = 0.0
    enabler_value: float = 0.0
    maintenance_tariff_total: float = 0.0
    topology: NetworkValidationReport = field(default_factory=NetworkValidationReport)
    storage_value: StorageValueBreakdown = field(default_factory=StorageValueBreakdown)
    synergy_notes: List[str] = field(default_factory=list)
    mounting_requirements: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
