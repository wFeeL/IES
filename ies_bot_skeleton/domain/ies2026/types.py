from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ObjectCategory = Literal["consumer", "generator", "storage", "infrastructure"]
IssueSeverity = Literal["critical", "warning", "hint"]
AuctionDirection = Literal["descending_consumer_tariff", "ascending_service_tariff"]
RiskBand = Literal["low", "medium", "high"]
LotProfile = Literal["consumer", "generator", "storage", "infrastructure", "mixed"]
AnalysisStage = Literal["pre_auction_lot_valuation", "post_auction_system_planning"]
PriceRole = Literal["consumer_floor", "service_ceiling"]


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
class WindLatentParams:
    cut_in_speed: float
    rated_speed: float
    cut_out_speed: float
    restart_speed_after_storm: float
    location_gain: float
    effective_power_scale: float
    plateau_shape: float
    hysteresis_gap: float
    inertia_tau: float
    damping: float = 0.0


@dataclass(slots=True)
class WindObservation:
    tick: int
    wind_speed_mps: float
    observed_output_mw: float
    channel: str = ""


@dataclass(slots=True)
class WindCalibrationDataset:
    observations: List[WindObservation] = field(default_factory=list)
    source: str = "shared_ruleset_prior"
    class_key: str = "wind"


@dataclass(slots=True)
class WindPosteriorSample:
    latent: WindLatentParams
    weight: float
    fit_error: float
    label: str = ""


@dataclass(slots=True)
class WindPosteriorSummary:
    posterior_mean_value: float = 0.0
    posterior_q25_value: float = 0.0
    posterior_q10_value: float = 0.0
    cvar_value: float = 0.0
    downside_expected_value: float = 0.0
    uncertainty_penalty: float = 0.0
    confidence_by_parameter: Dict[str, Dict[str, float]] = field(default_factory=dict)
    samples: List[WindPosteriorSample] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class UnifiedLotRankingRow:
    lot_id: int
    lot_name: str
    optimal_purchase_price: float
    price_role: PriceRole
    expected_profit: float
    risk_adjusted_profit: float
    direct_delta_profit: float
    synergy_value: float
    infrastructure_enabler_value: float
    topology_feasibility: str
    wind_uncertainty_penalty: float
    explanation: str


@dataclass(slots=True)
class CombinationRankingRow:
    lot_ids: List[int]
    title: str
    optimal_purchase_price_total: float
    expected_profit: float
    risk_adjusted_profit: float
    synergy: float
    direct_profit: float
    topology_status: str
    budget_fit: Dict[str, Any] = field(default_factory=dict)
    main_substation_dependency: str = ""
    wind_uncertainty_penalty: float = 0.0
    explanation: str = ""


@dataclass(slots=True)
class MarketBid:
    tick: int
    declared_mw: float
    anti_dumping_cap_mw: float
    useful_energy_mw: float
    actual_high_price_sale_mw: float
    low_price_sale_mw: float
    shortfall_mw: float
    high_price: float
    low_price: float
    balancing_penalty_price: float
    gp_purchase_mw: float = 0.0
    exchange_price_band: str = "2-20"


@dataclass(slots=True)
class StorageValueBreakdown:
    arbitrage: float = 0.0
    balancing: float = 0.0
    reserve: float = 0.0
    anti_dumping_support: float = 0.0


@dataclass(slots=True)
class SimulationTotals:
    consumer_revenue: float = 0.0
    fixed_tariff_revenue: float = 0.0
    market_revenue: float = 0.0
    exchange_sale_revenue: float = 0.0
    guaranteed_sale_revenue: float = 0.0
    market_purchase_cost: float = 0.0
    gp_purchase_cost: float = 0.0
    service_cost: float = 0.0
    loss_cost: float = 0.0
    balancing_penalty: float = 0.0
    unmet_load_penalty: float = 0.0
    flexibility_credit: float = 0.0
    reserve_credit: float = 0.0
    total_generation_mw: float = 0.0
    useful_generation_mw: float = 0.0
    useful_energy_export_mw: float = 0.0
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
    topology_candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)


@dataclass(slots=True)
class SimulationResult:
    totals: SimulationTotals
    topology: NetworkValidationReport
    market_bids: List[MarketBid] = field(default_factory=list)
    tick_rows: List[Dict[str, Any]] = field(default_factory=list)
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
    lot_profile: LotProfile
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
    bundle_synergy_value: float = 0.0
    maintenance_tariff_total: float = 0.0
    topology: NetworkValidationReport = field(default_factory=NetworkValidationReport)
    storage_value: StorageValueBreakdown = field(default_factory=StorageValueBreakdown)
    synergy_notes: List[str] = field(default_factory=list)
    mounting_requirements: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    minimum_acceptable_tariff: Optional[float] = None
    recommended_walkdown_tariff: Optional[float] = None
    aggressive_floor: Optional[float] = None
    hard_floor: Optional[float] = None
    maximum_acceptable_service_tariff: Optional[float] = None
    recommended_bid_ceiling: Optional[float] = None
    soft_ceiling: Optional[float] = None
    hard_ceiling: Optional[float] = None
    recommended_opening_bid: float = 0.0
    recommended_counter_bid: float = 0.0
    hard_limit: float = 0.0
    allpay_trigger_policy: str = ""
    drop_candidate_score: float = 0.0
    portfolio_substitute_group: str = ""
    plan_b_if_lost: str = ""
    plan_c_if_overbid: str = ""
    analysis_stage: AnalysisStage = "pre_auction_lot_valuation"
    optimal_purchase_price: float = 0.0
    price_role: PriceRole = "service_ceiling"
    expected_profit_after_purchase: float = 0.0
    risk_adjusted_profit: float = 0.0
    synergy_value: float = 0.0
    infrastructure_enabler_value: float = 0.0
    topology_feasibility: str = "feasible"
    wind_uncertainty_penalty: float = 0.0
    wind_posterior: WindPosteriorSummary = field(default_factory=WindPosteriorSummary)
    topology_risk_score: float = 0.0
    market_risk_score: float = 0.0
    balancing_risk_score: float = 0.0
    loss_risk_score: float = 0.0
