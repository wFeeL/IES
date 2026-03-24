from __future__ import annotations

import math
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
    WindCalibrationDataset,
    WindLatentParams,
    WindObservation,
    WindPosteriorSample,
    WindPosteriorSummary,
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


def _clip(value: float, lower: float, upper: float) -> float:
    return max(float(lower), min(float(upper), float(value)))


def _quantile(values: Sequence[float], q: float) -> float:
    rows = sorted(float(value) for value in values)
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    q = _clip(float(q), 0.0, 1.0)
    pos = (len(rows) - 1) * q
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return rows[lower]
    weight = pos - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    rows = sorted(
        (
            (float(value), max(0.0, float(weight)))
            for value, weight in zip(values, weights)
        ),
        key=lambda item: item[0],
    )
    if not rows:
        return 0.0
    total = sum(weight for _, weight in rows)
    if total <= 0.0:
        return _quantile([value for value, _weight in rows], q)
    target = _clip(float(q), 0.0, 1.0) * total
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative >= target:
            return value
    return rows[-1][0]


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total_weight = sum(max(0.0, float(weight)) for weight in weights)
    if total_weight <= 0.0:
        return _mean(values)
    return float(
        sum(float(value) * max(0.0, float(weight)) for value, weight in zip(values, weights))
        / total_weight
    )


def _weighted_tail_mean(values: Sequence[float], weights: Sequence[float], tail_share: float) -> float:
    rows = sorted(
        (
            (float(value), max(0.0, float(weight)))
            for value, weight in zip(values, weights)
            if max(0.0, float(weight)) > 0.0
        ),
        key=lambda item: item[0],
    )
    if not rows:
        return 0.0
    total = sum(weight for _, weight in rows)
    if total <= 0.0:
        return _quantile([value for value, _weight in rows], max(0.0, float(tail_share)))
    target = max(1e-9, total * _clip(float(tail_share), 0.05, 1.0))
    cumulative = 0.0
    weighted_sum = 0.0
    for value, weight in rows:
        remaining = target - cumulative
        take = min(weight, remaining)
        if take <= 0.0:
            break
        weighted_sum += value * take
        cumulative += take
        if cumulative >= target:
            break
    if cumulative <= 0.0:
        return rows[0][0]
    return weighted_sum / cumulative


def _risk_band(score: float) -> RiskBand:
    if score >= 0.55:
        return "high"
    if score >= 0.22:
        return "medium"
    return "low"


def _consumer_key(code: str) -> str:
    mapping = {
        "house_a": "house_a",
        "house_b": "house_b",
        "office": "office",
        "factory": "factory",
        "hospital": "hospital",
        "house": "house_a",
    }
    return mapping.get(_norm(code), "house_a")


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


def _solar_output(
    obj: EnergyObject,
    tick: ForecastTick,
    scenario: Dict[str, float],
    config: Dict[str, Any],
) -> float:
    params = dict(obj.parameters or {})
    defaults = dict((config.get("generation") or {}).get("solar") or {})
    max_power = _as_float((config.get("generation") or {}).get("max_power_mw", 20.0), 20.0)
    capacity = _as_float(
        params.get(
            "generation_mw",
            params.get("capacity_mw", defaults.get("default_capacity_mw", 20.0)),
        ),
        20.0,
    )
    capacity = min(capacity, max_power)
    efficiency = _as_float(params.get("efficiency", defaults.get("default_efficiency", 0.94)), 0.94)
    illumination = max(
        0.0,
        float(tick.illumination) * _as_float(scenario.get("illumination", 1.0), 1.0),
    )
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
    location_hint = _as_float(params.get("location_gain_hint", 1.0), 1.0)
    return max(0.0, float(value) * _as_float(scenario.get("wind", 1.0), 1.0) * location_hint)


def _usable_fraction(obj: EnergyObject, topology: NetworkValidationReport) -> float:
    return float(topology.usable_fraction_by_object.get(obj.object_id, 1.0) or 0.0)


def _aggregate_storage(
    objects: Sequence[EnergyObject],
    topology: NetworkValidationReport,
    config: Dict[str, Any],
) -> Dict[str, float]:
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
        capacity += _as_float(
            params.get("capacity_mw_tick", storage_cfg.get("capacity_mw_tick", 120.0)),
            120.0,
        ) * usable
        charge += _as_float(
            params.get(
                "charge_rate_mw_tick",
                params.get("charge_rate_mw", storage_cfg.get("charge_rate_mw_tick", 15.0)),
            ),
            15.0,
        ) * usable
        discharge += _as_float(
            params.get(
                "discharge_rate_mw_tick",
                params.get("discharge_rate_mw", storage_cfg.get("discharge_rate_mw_tick", 20.0)),
            ),
            20.0,
        ) * usable
        efficiency += _as_float(
            params.get("roundtrip_efficiency", storage_cfg.get("roundtrip_efficiency", 0.93)),
            0.93,
        )
        reserve_floor += _as_float(
            params.get("reserve_floor_share", storage_cfg.get("reserve_floor_share", 0.2)),
            0.2,
        )
        loss += _as_float(topology.loss_fraction_by_object.get(obj.object_id, 0.0), 0.0)
        count += 1.0
    if count <= 0:
        return {
            "capacity": 0.0,
            "charge": 0.0,
            "discharge": 0.0,
            "efficiency": 1.0,
            "reserve_floor_share": 0.0,
            "loss": 0.0,
        }
    return {
        "capacity": capacity,
        "charge": charge,
        "discharge": discharge,
        "efficiency": max(0.65, efficiency / count),
        "reserve_floor_share": max(0.0, min(0.7, reserve_floor / count)),
        "loss": max(0.0, min(0.35, loss / count)),
    }


def _clone_objects(objects: Iterable[EnergyObject]) -> List[EnergyObject]:
    return [deepcopy(obj) for obj in ensure_terminals(objects)]


def _candidate_tariff_total(objects: Sequence[EnergyObject], direction: AuctionDirection) -> float:
    total = 0.0
    for obj in objects:
        if not obj.is_candidate:
            continue
        total += _consumer_tariff(obj) if direction == "descending_consumer_tariff" else _service_cost_per_tick(obj)
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


def _set_candidate_tariff(
    objects: Sequence[EnergyObject],
    value: float,
    direction: AuctionDirection,
) -> None:
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
            params["service_tariff_rub_per_tick"] = per_object
        obj.parameters = params


def _topology_feasibility(report: NetworkValidationReport) -> str:
    if report.blocking:
        return "blocked"
    if any(issue.severity == "warning" for issue in report.issues):
        return "conditional"
    return "feasible"


def _wind_target_output(
    *,
    speed: float,
    latent: WindLatentParams,
    rated_power: float,
    max_power: float,
) -> float:
    if speed <= 0.0 or speed >= latent.cut_out_speed:
        return 0.0
    start_threshold = latent.cut_in_speed
    if speed < start_threshold:
        return 0.0
    cap = min(float(max_power), float(rated_power)) * latent.location_gain * latent.effective_power_scale
    cap = min(float(max_power), max(0.0, cap))
    if speed >= latent.rated_speed:
        rated_band = max(1.0, latent.cut_out_speed - latent.rated_speed)
        extra = min(1.0, max(0.0, (speed - latent.rated_speed) / rated_band))
        plateau = latent.plateau_shape + (1.0 - latent.plateau_shape) * extra
        return cap * _clip(plateau, 0.75, 1.0)
    span = max(0.25, latent.rated_speed - latent.cut_in_speed)
    fraction = _clip((speed - latent.cut_in_speed) / span, 0.0, 1.0)
    return cap * (fraction**3)


def _simulate_wind_series_for_latent(
    *,
    speeds: Sequence[float],
    rated_power: float,
    max_power: float,
    latent: WindLatentParams,
) -> List[float]:
    series: List[float] = []
    prev_output = 0.0
    prev_speed = 0.0
    storm_locked = False
    off_due_to_low_wind = True
    for raw_speed in speeds:
        speed = max(0.0, float(raw_speed))
        target = 0.0
        restart_threshold = _clip(
            latent.restart_speed_after_storm,
            latent.cut_in_speed,
            max(latent.cut_in_speed, latent.cut_out_speed - 0.2),
        )
        startup_threshold = latent.cut_in_speed
        if off_due_to_low_wind:
            startup_threshold += latent.hysteresis_gap * 0.35
        if speed >= latent.cut_out_speed:
            storm_locked = True
            target = 0.0
        elif storm_locked and speed > restart_threshold:
            target = 0.0
        else:
            if storm_locked and speed <= restart_threshold:
                storm_locked = False
            if speed < startup_threshold and prev_output <= 1e-6:
                off_due_to_low_wind = True
                target = 0.0
            else:
                target = _wind_target_output(
                    speed=speed,
                    latent=latent,
                    rated_power=rated_power,
                    max_power=max_power,
                )
                off_due_to_low_wind = target <= 1e-6
        speed_jump = abs(speed - prev_speed)
        damping_factor = max(
            0.72,
            1.0 - latent.damping * min(1.0, speed_jump / max(1.0, latent.cut_out_speed)),
        )
        target *= damping_factor
        alpha = 1.0 - math.exp(-1.0 / max(1.0, latent.inertia_tau / 4.0))
        output = prev_output + alpha * (target - prev_output)
        output = _clip(output, 0.0, min(max_power, rated_power))
        if storm_locked:
            output = 0.0
        series.append(output)
        prev_output = output
        prev_speed = speed
    return series


class WindPriorModel:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.wind_cfg = dict((config.get("generation") or {}).get("wind") or {})
        self.defaults = {
            "cut_in_speed": _as_float(self.wind_cfg.get("cut_in_mps", 3.0), 3.0),
            "rated_speed": _as_float(self.wind_cfg.get("rated_mps", 11.0), 11.0),
            "cut_out_speed": _as_float(self.wind_cfg.get("cut_out_mps", 25.0), 25.0),
            "restart_speed_after_storm": max(
                14.0,
                _as_float(self.wind_cfg.get("cut_out_mps", 25.0), 25.0) - 6.0,
            ),
            "location_gain": 1.0,
            "effective_power_scale": 0.95,
            "plateau_shape": 0.96,
            "hysteresis_gap": 3.0,
            "inertia_tau": 14.0,
            "damping": 0.04,
        }
        self.prior_cfg = dict(self.wind_cfg.get("prior") or {})

    def _range(self, key: str, default: Tuple[float, float]) -> Tuple[float, float]:
        raw = list(self.prior_cfg.get(key) or [])
        if len(raw) >= 2:
            low = _as_float(raw[0], default[0])
            high = _as_float(raw[1], default[1])
            return (min(low, high), max(low, high))
        return default

    def family_for_object(self, obj: EnergyObject) -> List[Tuple[str, WindLatentParams]]:
        params = dict(obj.parameters or {})
        ranges = {
            "cut_in_speed": self._range("cut_in_speed_range", (2.5, 4.2)),
            "rated_speed": self._range("rated_speed_range", (9.0, 12.8)),
            "cut_out_speed": self._range("cut_out_speed_range", (22.0, 28.0)),
            "restart_speed_after_storm": self._range("restart_speed_range", (15.0, 22.0)),
            "location_gain": self._range("location_gain_range", (0.82, 1.18)),
            "effective_power_scale": self._range("effective_power_scale_range", (0.78, 1.0)),
            "plateau_shape": self._range("plateau_shape_range", (0.88, 1.0)),
            "hysteresis_gap": self._range("hysteresis_gap_range", (1.5, 5.5)),
            "inertia_tau": self._range("inertia_tau_range", (6.0, 30.0)),
            "damping": self._range("damping_range", (0.0, 0.12)),
        }
        base = {
            "cut_in_speed": _clip(
                _as_float(params.get("cut_in_mps", self.defaults["cut_in_speed"]), self.defaults["cut_in_speed"]),
                *ranges["cut_in_speed"],
            ),
            "rated_speed": _clip(
                _as_float(params.get("rated_mps", self.defaults["rated_speed"]), self.defaults["rated_speed"]),
                *ranges["rated_speed"],
            ),
            "cut_out_speed": _clip(
                _as_float(params.get("cut_out_mps", self.defaults["cut_out_speed"]), self.defaults["cut_out_speed"]),
                *ranges["cut_out_speed"],
            ),
            "restart_speed_after_storm": _clip(
                _as_float(
                    params.get("restart_speed_after_storm", self.defaults["restart_speed_after_storm"]),
                    self.defaults["restart_speed_after_storm"],
                ),
                *ranges["restart_speed_after_storm"],
            ),
            "location_gain": _clip(
                _as_float(params.get("location_gain_hint", self.defaults["location_gain"]), self.defaults["location_gain"]),
                *ranges["location_gain"],
            ),
            "effective_power_scale": _clip(
                _as_float(params.get("effective_power_scale", self.defaults["effective_power_scale"]), self.defaults["effective_power_scale"]),
                *ranges["effective_power_scale"],
            ),
            "plateau_shape": _clip(
                _as_float(params.get("plateau_shape", self.defaults["plateau_shape"]), self.defaults["plateau_shape"]),
                *ranges["plateau_shape"],
            ),
            "hysteresis_gap": _clip(
                _as_float(params.get("hysteresis_gap", self.defaults["hysteresis_gap"]), self.defaults["hysteresis_gap"]),
                *ranges["hysteresis_gap"],
            ),
            "inertia_tau": _clip(
                _as_float(params.get("inertia_tau", self.defaults["inertia_tau"]), self.defaults["inertia_tau"]),
                *ranges["inertia_tau"],
            ),
            "damping": _clip(
                _as_float(params.get("damping", self.defaults["damping"]), self.defaults["damping"]),
                *ranges["damping"],
            ),
        }

        def span(name: str) -> float:
            low, high = ranges[name]
            return max(1e-6, high - low)

        def latent(**overrides: float) -> WindLatentParams:
            merged = dict(base)
            merged.update(overrides)
            merged["cut_in_speed"] = _clip(merged["cut_in_speed"], *ranges["cut_in_speed"])
            merged["rated_speed"] = _clip(
                max(merged["rated_speed"], merged["cut_in_speed"] + 1.0),
                *ranges["rated_speed"],
            )
            merged["cut_out_speed"] = _clip(
                max(merged["cut_out_speed"], merged["rated_speed"] + 6.0),
                *ranges["cut_out_speed"],
            )
            merged["restart_speed_after_storm"] = _clip(
                min(merged["restart_speed_after_storm"], merged["cut_out_speed"] - 0.2),
                *ranges["restart_speed_after_storm"],
            )
            merged["location_gain"] = _clip(merged["location_gain"], *ranges["location_gain"])
            merged["effective_power_scale"] = _clip(
                merged["effective_power_scale"],
                *ranges["effective_power_scale"],
            )
            merged["plateau_shape"] = _clip(merged["plateau_shape"], *ranges["plateau_shape"])
            merged["hysteresis_gap"] = _clip(merged["hysteresis_gap"], *ranges["hysteresis_gap"])
            merged["inertia_tau"] = _clip(merged["inertia_tau"], *ranges["inertia_tau"])
            merged["damping"] = _clip(merged["damping"], *ranges["damping"])
            return WindLatentParams(**merged)

        presets = [
            (
                "downside",
                latent(
                    cut_in_speed=base["cut_in_speed"] + span("cut_in_speed") * 0.18,
                    rated_speed=base["rated_speed"] + span("rated_speed") * 0.20,
                    cut_out_speed=base["cut_out_speed"] - span("cut_out_speed") * 0.18,
                    restart_speed_after_storm=base["restart_speed_after_storm"] - span("restart_speed_after_storm") * 0.15,
                    location_gain=base["location_gain"] - span("location_gain") * 0.22,
                    effective_power_scale=base["effective_power_scale"] - span("effective_power_scale") * 0.20,
                    plateau_shape=base["plateau_shape"] - span("plateau_shape") * 0.12,
                    hysteresis_gap=base["hysteresis_gap"] + span("hysteresis_gap") * 0.22,
                    inertia_tau=base["inertia_tau"] + span("inertia_tau") * 0.22,
                    damping=base["damping"] + span("damping") * 0.20,
                ),
            ),
            (
                "q10",
                latent(
                    cut_in_speed=base["cut_in_speed"] + span("cut_in_speed") * 0.10,
                    rated_speed=base["rated_speed"] + span("rated_speed") * 0.12,
                    cut_out_speed=base["cut_out_speed"] - span("cut_out_speed") * 0.10,
                    location_gain=base["location_gain"] - span("location_gain") * 0.10,
                    effective_power_scale=base["effective_power_scale"] - span("effective_power_scale") * 0.10,
                    plateau_shape=base["plateau_shape"] - span("plateau_shape") * 0.05,
                    hysteresis_gap=base["hysteresis_gap"] + span("hysteresis_gap") * 0.10,
                    inertia_tau=base["inertia_tau"] + span("inertia_tau") * 0.10,
                    damping=base["damping"] + span("damping") * 0.08,
                ),
            ),
            ("q25", latent()),
            (
                "base",
                latent(
                    effective_power_scale=base["effective_power_scale"] + span("effective_power_scale") * 0.05,
                    plateau_shape=base["plateau_shape"] + span("plateau_shape") * 0.02,
                ),
            ),
            (
                "q75",
                latent(
                    cut_in_speed=base["cut_in_speed"] - span("cut_in_speed") * 0.08,
                    rated_speed=base["rated_speed"] - span("rated_speed") * 0.08,
                    cut_out_speed=base["cut_out_speed"] + span("cut_out_speed") * 0.08,
                    location_gain=base["location_gain"] + span("location_gain") * 0.08,
                    effective_power_scale=base["effective_power_scale"] + span("effective_power_scale") * 0.08,
                    plateau_shape=base["plateau_shape"] + span("plateau_shape") * 0.03,
                    hysteresis_gap=base["hysteresis_gap"] - span("hysteresis_gap") * 0.08,
                    inertia_tau=base["inertia_tau"] - span("inertia_tau") * 0.08,
                    damping=max(ranges["damping"][0], base["damping"] - span("damping") * 0.05),
                ),
            ),
            (
                "upside",
                latent(
                    cut_in_speed=base["cut_in_speed"] - span("cut_in_speed") * 0.16,
                    rated_speed=base["rated_speed"] - span("rated_speed") * 0.18,
                    cut_out_speed=base["cut_out_speed"] + span("cut_out_speed") * 0.12,
                    location_gain=base["location_gain"] + span("location_gain") * 0.18,
                    effective_power_scale=base["effective_power_scale"] + span("effective_power_scale") * 0.14,
                    plateau_shape=base["plateau_shape"] + span("plateau_shape") * 0.06,
                    hysteresis_gap=base["hysteresis_gap"] - span("hysteresis_gap") * 0.14,
                    inertia_tau=base["inertia_tau"] - span("inertia_tau") * 0.14,
                    damping=max(ranges["damping"][0], base["damping"] - span("damping") * 0.08),
                ),
            ),
            (
                "storm_recovery",
                latent(
                    restart_speed_after_storm=base["restart_speed_after_storm"] + span("restart_speed_after_storm") * 0.18,
                    hysteresis_gap=base["hysteresis_gap"] + span("hysteresis_gap") * 0.14,
                    inertia_tau=base["inertia_tau"] + span("inertia_tau") * 0.08,
                ),
            ),
        ]
        sample_limit = max(3, int(self.prior_cfg.get("posterior_sample_limit", 7) or 7))
        return presets[:sample_limit]


class WindPosteriorEstimator:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.prior = WindPriorModel(config)
        self.wind_cfg = dict((config.get("generation") or {}).get("wind") or {})
        self.max_power = _as_float(self.wind_cfg.get("max_power_mw", 20.0), 20.0)
        self._cache: Dict[Tuple[str, Tuple[Tuple[float, float], ...]], WindPosteriorSummary] = {}

    def _object_rated_power(self, obj: EnergyObject) -> float:
        params = dict(obj.parameters or {})
        return min(
            self.max_power,
            _as_float(
                params.get(
                    "generation_mw",
                    params.get("rated_power_mw", self.wind_cfg.get("rated_power_mw", 18.0)),
                ),
                18.0,
            ),
        )

    def _shared_dataset(self) -> WindCalibrationDataset:
        dataset_cfg = dict(self.wind_cfg.get("shared_calibration_dataset") or {})
        observations: List[WindObservation] = []
        for row in list(dataset_cfg.get("observations") or []):
            if not isinstance(row, dict):
                continue
            observations.append(
                WindObservation(
                    tick=int(row.get("tick", len(observations)) or len(observations)),
                    wind_speed_mps=_as_float(row.get("wind_speed_mps", row.get("wind_speed", 0.0)), 0.0),
                    observed_output_mw=_as_float(row.get("observed_output_mw", row.get("output_mw", 0.0)), 0.0),
                    channel=str(row.get("channel") or ""),
                )
            )
        return WindCalibrationDataset(
            observations=observations,
            source=str(dataset_cfg.get("source") or "shared_ruleset_prior"),
            class_key="wind",
        )

    def _object_specific_dataset(self, obj: EnergyObject) -> WindCalibrationDataset:
        params = dict(obj.parameters or {})
        observations: List[WindObservation] = []
        extra_rows = list(
            params.get("wind_calibration_observations")
            or params.get("wind_observations")
            or []
        )
        for row in extra_rows:
            if not isinstance(row, dict):
                continue
            observations.append(
                WindObservation(
                    tick=int(row.get("tick", len(observations)) or len(observations)),
                    wind_speed_mps=_as_float(row.get("wind_speed_mps", row.get("wind_speed", 0.0)), 0.0),
                    observed_output_mw=_as_float(row.get("observed_output_mw", row.get("output_mw", 0.0)), 0.0),
                    channel=str(row.get("channel") or ""),
                )
            )
        observations.sort(key=lambda row: int(row.tick))
        return WindCalibrationDataset(
            observations=observations,
            source="object_specific_dataset" if observations else "empty_object_dataset",
            class_key="wind",
        )

    def _object_dataset(self, obj: EnergyObject) -> WindCalibrationDataset:
        shared = self._shared_dataset()
        specific = self._object_specific_dataset(obj)
        observations = [*shared.observations, *specific.observations]
        observations.sort(key=lambda row: int(row.tick))
        return WindCalibrationDataset(
            observations=observations,
            source="object_augmented_dataset" if specific.observations else shared.source,
            class_key="wind",
        )

    def _observed_threshold_hints(
        self,
        dataset: WindCalibrationDataset,
    ) -> Dict[str, Dict[str, float]]:
        observations = list(dataset.observations)
        if not observations:
            return {}
        positive_rows = sorted(
            (
                (float(row.wind_speed_mps), float(row.observed_output_mw))
                for row in observations
                if float(row.observed_output_mw) > 0.25
            ),
            key=lambda item: item[0],
        )
        zero_rows = sorted(
            (
                (float(row.wind_speed_mps), float(row.observed_output_mw))
                for row in observations
                if float(row.observed_output_mw) <= 0.15
            ),
            key=lambda item: item[0],
        )
        if not positive_rows:
            return {}
        hints: Dict[str, Dict[str, float]] = {}
        first_positive_speed = float(positive_rows[0][0])
        last_zero_before_positive = [
            float(speed) for speed, _output in zero_rows if float(speed) <= first_positive_speed
        ]
        if last_zero_before_positive:
            cut_in_low = max(last_zero_before_positive)
            cut_in_high = first_positive_speed
            cut_in_mid = (cut_in_low + cut_in_high) / 2.0
        else:
            cut_in_low = max(0.0, first_positive_speed - 0.8)
            cut_in_high = first_positive_speed
            cut_in_mid = max(cut_in_low, first_positive_speed - 0.4)
        hints["cut_in_speed"] = {
            "low": round(cut_in_low, 4),
            "mid": round(cut_in_mid, 4),
            "high": round(cut_in_high, 4),
        }

        max_output = max(float(output) for _speed, output in positive_rows)
        plateau_rows = [
            (float(speed), float(output))
            for speed, output in positive_rows
            if float(output) >= max_output * 0.9
        ]
        if plateau_rows:
            plateau_low = min(float(speed) for speed, _output in plateau_rows)
            plateau_high = max(float(speed) for speed, _output in plateau_rows)
            hints["rated_speed"] = {
                "low": round(plateau_low, 4),
                "mid": round((plateau_low + plateau_high) / 2.0, 4),
                "high": round(plateau_high, 4),
            }
            shutdown_rows = [
                float(speed)
                for speed, _output in zero_rows
                if float(speed) >= plateau_low
            ]
            if shutdown_rows:
                cut_out_low = min(shutdown_rows)
                cut_out_high = max(shutdown_rows)
                hints["cut_out_speed"] = {
                    "low": round(cut_out_low, 4),
                    "mid": round(cut_out_low, 4),
                    "high": round(cut_out_high, 4),
                }
        return hints

    def _fit_error(
        self,
        *,
        obj: EnergyObject,
        latent: WindLatentParams,
        dataset: WindCalibrationDataset,
    ) -> float:
        if not dataset.observations:
            return 1.0
        speeds = [float(row.wind_speed_mps) for row in dataset.observations]
        observed = [float(row.observed_output_mw) for row in dataset.observations]
        rated_power = self._object_rated_power(obj)
        predicted = _simulate_wind_series_for_latent(
            speeds=speeds,
            rated_power=rated_power,
            max_power=self.max_power,
            latent=latent,
        )
        abs_errors = [abs(p - o) for p, o in zip(predicted, observed)]
        mse = _mean(error * error for error in abs_errors)
        threshold_penalty = 0.0
        for speed, observed_output, predicted_output in zip(speeds, observed, predicted):
            if observed_output <= 0.15 and predicted_output > 0.15:
                threshold_penalty += predicted_output * (1.2 if speed < latent.rated_speed else 0.6)
            if observed_output > 0.25 and speed <= latent.cut_in_speed:
                threshold_penalty += (observed_output - predicted_output) * 0.8
        low_speed_outputs = [
            predicted[idx]
            for idx, speed in enumerate(speeds)
            if speed < latent.cut_in_speed and idx < len(predicted)
        ]
        if low_speed_outputs:
            threshold_penalty += _mean(low_speed_outputs) * 0.5
        overshoot = max(0.0, max(predicted or [0.0]) - self.max_power)
        threshold_penalty += overshoot * 1.5
        return math.sqrt(max(0.0, mse)) + threshold_penalty

    def estimate(self, obj: EnergyObject) -> WindPosteriorSummary:
        dataset = self._object_dataset(obj)
        object_specific = self._object_specific_dataset(obj)
        shared_dataset = self._shared_dataset()
        key = (
            str(obj.object_id),
            tuple(
                (
                    round(float(row.wind_speed_mps), 3),
                    round(float(row.observed_output_mw), 3),
                )
                for row in dataset.observations
            ),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        prior_family = self.prior.family_for_object(obj)
        empirical_dataset = object_specific if object_specific.observations else dataset
        threshold_hints = self._observed_threshold_hints(empirical_dataset)
        if empirical_dataset.observations:
            positive_speeds = [
                float(row.wind_speed_mps)
                for row in empirical_dataset.observations
                if float(row.observed_output_mw) > 0.25
            ]
            zero_speeds = [
                float(row.wind_speed_mps)
                for row in empirical_dataset.observations
                if float(row.observed_output_mw) <= 0.15
            ]
            if positive_speeds:
                empirical_cut_in = max(
                    2.5,
                    min(positive_speeds) - 0.6,
                    max([speed for speed in zero_speeds if speed < min(positive_speeds)] or [2.5]),
                )
                empirical_rated = max(min(positive_speeds) + 3.0, max(positive_speeds))
                empirical_cut_out = max(
                    [
                        float(row.wind_speed_mps)
                        for row in empirical_dataset.observations
                        if float(row.observed_output_mw) <= 0.15
                        and float(row.wind_speed_mps) >= empirical_rated
                    ]
                    or [
                        float(row.wind_speed_mps)
                        for row in dataset.observations
                        if float(row.observed_output_mw) <= 0.15
                        and float(row.wind_speed_mps) >= empirical_rated
                    ]
                    or [25.0]
                )
                empirical_restart = min(empirical_cut_out - 0.5, max(empirical_cut_in + 4.0, empirical_cut_out - 6.0))
                base_latent = prior_family[min(len(prior_family) - 1, len(prior_family) // 2)][1]
                empirical_latent = WindLatentParams(
                    cut_in_speed=_clip(empirical_cut_in, 2.5, 6.5),
                    rated_speed=_clip(empirical_rated, empirical_cut_in + 1.0, 14.5),
                    cut_out_speed=_clip(empirical_cut_out, empirical_rated + 4.0, 28.0),
                    restart_speed_after_storm=_clip(empirical_restart, empirical_cut_in + 1.0, max(empirical_cut_in + 1.0, empirical_cut_out - 0.2)),
                    location_gain=base_latent.location_gain,
                    effective_power_scale=base_latent.effective_power_scale,
                    plateau_shape=base_latent.plateau_shape,
                    hysteresis_gap=base_latent.hysteresis_gap,
                    inertia_tau=base_latent.inertia_tau,
                    damping=base_latent.damping,
                )
                prior_family = [("empirical_fit", empirical_latent), *prior_family]
        if not prior_family:
            summary = WindPosteriorSummary(notes=["wind prior family is empty"])
            self._cache[key] = summary
            return summary
        errors: List[float] = []
        object_specific_weight = 0.75 if object_specific.observations else 1.0
        shared_weight = 0.25 if object_specific.observations and shared_dataset.observations else 0.0
        normalizer = max(1e-9, object_specific_weight + shared_weight)
        object_specific_weight /= normalizer
        shared_weight /= normalizer
        for _label, latent in prior_family:
            if object_specific.observations:
                object_error = self._fit_error(obj=obj, latent=latent, dataset=object_specific)
                shared_error = (
                    self._fit_error(obj=obj, latent=latent, dataset=shared_dataset)
                    if shared_weight > 0.0
                    else 0.0
                )
                errors.append(object_error * object_specific_weight + shared_error * shared_weight)
            else:
                errors.append(self._fit_error(obj=obj, latent=latent, dataset=dataset))
        avg_error = _mean(errors) or 1.0
        raw_weights = []
        for (label, _latent), error in zip(prior_family, errors):
            weight = math.exp(-error / max(0.25, avg_error))
            if label == "empirical_fit":
                weight *= 5.0 if object_specific.observations else 3.5
            raw_weights.append(weight)
        weight_total = sum(raw_weights) or 1.0
        samples: List[WindPosteriorSample] = []
        for (label, latent), error, weight in zip(prior_family, errors, raw_weights):
            samples.append(
                WindPosteriorSample(
                    latent=latent,
                    fit_error=round(float(error), 6),
                    weight=float(weight / weight_total),
                    label=label,
                )
            )
        confidence_by_parameter: Dict[str, Dict[str, float]] = {}
        for name in (
            "cut_in_speed",
            "rated_speed",
            "cut_out_speed",
            "restart_speed_after_storm",
            "location_gain",
            "effective_power_scale",
            "plateau_shape",
            "hysteresis_gap",
            "inertia_tau",
            "damping",
        ):
            values = [float(getattr(sample.latent, name)) for sample in samples]
            weights = [float(sample.weight) for sample in samples]
            confidence_by_parameter[name] = {
                "low": round(_weighted_quantile(values, weights, 0.1), 4),
                "mid": round(_weighted_mean(values, weights), 4),
                "high": round(_weighted_quantile(values, weights, 0.9), 4),
            }
        for name, interval in threshold_hints.items():
            confidence_by_parameter[name] = dict(interval)
        productivity = [
            float(sample.latent.location_gain * sample.latent.effective_power_scale)
            for sample in samples
        ]
        weights = [float(sample.weight) for sample in samples]
        mean_value = _weighted_mean(productivity, weights)
        q25_value = _weighted_quantile(productivity, weights, 0.25)
        q10_value = _weighted_quantile(productivity, weights, 0.10)
        cvar_value = _weighted_tail_mean(
            productivity,
            weights,
            _as_float(dict(self.wind_cfg.get("prior") or {}).get("cvar_tail_share", 0.2), 0.2),
        )
        downside = 0.5 * q25_value + 0.3 * mean_value + 0.2 * q10_value
        summary = WindPosteriorSummary(
            posterior_mean_value=round(mean_value, 4),
            posterior_q25_value=round(q25_value, 4),
            posterior_q10_value=round(q10_value, 4),
            cvar_value=round(cvar_value, 4),
            downside_expected_value=round(downside, 4),
            uncertainty_penalty=round(max(0.0, mean_value - downside), 4),
            confidence_by_parameter=confidence_by_parameter,
            samples=samples,
            notes=[
                (
                    "Posterior обновлён по calibration dataset, скрытые thresholds не "
                    "считаются заранее известной истиной."
                ),
                f"Источник калибровки: {dataset.source}. Наблюдений: {len(dataset.observations)}.",
                (
                    "Object-specific wind observations override shared onset prior where "
                    "auction-time evidence is available."
                    if object_specific.observations
                    else "Использован shared calibration dataset как аукционный prior по классу ВЭС."
                ),
            ],
        )
        self._cache[key] = summary
        return summary


class WindAuctionValuator:
    def __init__(self, *, config: Dict[str, Any], dataset: ForecastDataset) -> None:
        self.config = config
        self.dataset = dataset
        self.wind_cfg = dict((config.get("generation") or {}).get("wind") or {})
        self.prior_cfg = dict(self.wind_cfg.get("prior") or {})
        self.max_power = _as_float(self.wind_cfg.get("max_power_mw", 20.0), 20.0)
        self.posterior_estimator = WindPosteriorEstimator(config)
        self._sample_series_cache: Dict[Tuple[str, str], List[Tuple[WindPosteriorSample, List[float]]]] = {}
        self._series_cache: Dict[Tuple[str, str, str], List[float]] = {}

    def posterior_summary(self, obj: EnergyObject) -> WindPosteriorSummary:
        return self.posterior_estimator.estimate(obj)

    def _object_rated_power(self, obj: EnergyObject) -> float:
        params = dict(obj.parameters or {})
        return min(
            self.max_power,
            _as_float(
                params.get(
                    "generation_mw",
                    params.get("rated_power_mw", self.wind_cfg.get("rated_power_mw", 18.0)),
                ),
                18.0,
            ),
        )

    def _sample_series(
        self,
        *,
        obj: EnergyObject,
        scenario_label: str,
    ) -> List[Tuple[WindPosteriorSample, List[float]]]:
        key = (str(obj.object_id), str(scenario_label))
        cached = self._sample_series_cache.get(key)
        if cached is not None:
            return cached
        scenario = scenario_coefficients(self.config, scenario_label)
        summary = self.posterior_summary(obj)
        speeds = [_wind_speed_for_object(obj, tick, scenario) for tick in self.dataset.ticks]
        rated_power = self._object_rated_power(obj)
        rows = [
            (
                sample,
                _simulate_wind_series_for_latent(
                    speeds=speeds,
                    rated_power=rated_power,
                    max_power=self.max_power,
                    latent=sample.latent,
                ),
            )
            for sample in summary.samples
        ]
        self._sample_series_cache[key] = rows
        return rows

    def series_for_object(
        self,
        *,
        obj: EnergyObject,
        scenario_label: str,
        mode: str = "robust",
    ) -> List[float]:
        key = (str(obj.object_id), str(scenario_label), str(mode))
        cached = self._series_cache.get(key)
        if cached is not None:
            return cached
        rows = self._sample_series(obj=obj, scenario_label=scenario_label)
        if not rows:
            return [0.0 for _tick in self.dataset.ticks]
        weights = [float(sample.weight) for sample, _series in rows]
        robust_weights = dict(self.prior_cfg.get("robust_value_weights") or {})
        q25_weight = _as_float(robust_weights.get("q25", 0.5), 0.5)
        mean_weight = _as_float(robust_weights.get("mean", 0.3), 0.3)
        q10_weight = _as_float(robust_weights.get("q10", 0.2), 0.2)
        tail_share = _as_float(self.prior_cfg.get("cvar_tail_share", 0.2), 0.2)
        out: List[float] = []
        for tick_index in range(len(self.dataset.ticks)):
            values = [float(series[tick_index]) for _sample, series in rows]
            if mode == "mean":
                out.append(_weighted_mean(values, weights))
            elif mode == "q25":
                out.append(_weighted_quantile(values, weights, 0.25))
            elif mode == "q10":
                out.append(_weighted_quantile(values, weights, 0.10))
            elif mode == "cvar":
                out.append(_weighted_tail_mean(values, weights, tail_share))
            else:
                mean_value = _weighted_mean(values, weights)
                q25_value = _weighted_quantile(values, weights, 0.25)
                q10_value = _weighted_quantile(values, weights, 0.10)
                out.append(q25_value * q25_weight + mean_value * mean_weight + q10_value * q10_weight)
        params = dict(obj.parameters or {})
        explicit_cut_out = params.get("cut_out_mps")
        if explicit_cut_out is not None:
            scenario = scenario_coefficients(self.config, scenario_label)
            speeds = [_wind_speed_for_object(obj, tick, scenario) for tick in self.dataset.ticks]
            cut_out_speed = _as_float(explicit_cut_out, self.wind_cfg.get("cut_out_mps", 25.0))
            restart_speed = _as_float(
                params.get("restart_speed_after_storm"),
                max(14.0, cut_out_speed - 6.0),
            )
            storm_locked = False
            guarded: List[float] = []
            for speed, value in zip(speeds, out):
                if float(speed) >= cut_out_speed:
                    storm_locked = True
                    guarded.append(0.0)
                    continue
                if storm_locked:
                    if float(speed) > restart_speed:
                        guarded.append(0.0)
                        continue
                    storm_locked = False
                guarded.append(max(0.0, float(value)))
            out = guarded
        self._series_cache[key] = out
        return out

    def output_for_tick(
        self,
        *,
        obj: EnergyObject,
        tick_index: int,
        scenario_label: str,
        mode: str = "robust",
    ) -> float:
        series = self.series_for_object(obj=obj, scenario_label=scenario_label, mode=mode)
        if tick_index < 0 or tick_index >= len(series):
            return 0.0
        return float(series[tick_index])


def _wind_output(
    *,
    obj: EnergyObject,
    tick: ForecastTick,
    tick_index: int,
    scenario: Dict[str, float],
    scenario_label: str,
    config: Dict[str, Any],
    wind_mode: str = "robust",
    wind_valuator: WindAuctionValuator | None = None,
) -> float:
    if wind_valuator is None:
        params = dict(obj.parameters or {})
        defaults = dict((config.get("generation") or {}).get("wind") or {})
        max_power = _as_float((config.get("generation") or {}).get("max_power_mw", 20.0), 20.0)
        rated_power = min(
            max_power,
            _as_float(
                params.get(
                    "generation_mw",
                    params.get("rated_power_mw", defaults.get("rated_power_mw", 18.0)),
                ),
                18.0,
            ),
        )
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
    return wind_valuator.output_for_tick(
        obj=obj,
        tick_index=tick_index,
        scenario_label=scenario_label,
        mode=wind_mode,
    )


def _simulate(
    *,
    objects: Sequence[EnergyObject],
    dataset: ForecastDataset,
    config: Dict[str, Any],
    scenario_label: str,
    declared_sales: Sequence[float] | None = None,
    wind_mode: str = "robust",
    wind_valuator: WindAuctionValuator | None = None,
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
                    gross_output = _wind_output(
                        obj=obj,
                        tick=tick,
                        tick_index=idx,
                        scenario=scenario,
                        scenario_label=scenario_label,
                        config=config,
                        wind_mode=wind_mode,
                        wind_valuator=wind_valuator,
                    )
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
        totals.service_cost += tick_service_cost
        totals.unmet_load_penalty += tick_unserved_penalty

        net_root = tick_generation_root - tick_load_root
        totals.useful_generation_mw += tick_generation_root

        if storage_cfg["capacity"] > 0.0:
            reserve_floor = storage_cfg["capacity"] * storage_cfg["reserve_floor_share"]
            future_window = future_buy_prices[idx + 1 : idx + 7]
            future_peak = max(future_window) if future_window else buy_price
            declared_sale_hint = 0.0
            if declared_sales is not None and idx < len(declared_sales):
                declared_sale_hint = float(declared_sales[idx] or 0.0)
            if net_root < 0.0 and soc > reserve_floor:
                discharge = min(-net_root, storage_cfg["discharge"], max(0.0, soc - reserve_floor))
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
            declared_sale = min(anti_dump_cap, max(0.0, useful_energy - risk_buffer))
        else:
            declared_sale_input = 0.0
            if idx < len(declared_sales):
                declared_sale_input = float(declared_sales[idx] or 0.0)
            declared_sale = min(anti_dump_cap, max(0.0, declared_sale_input))
        shortfall = max(0.0, declared_sale - useful_energy)
        actual_high_sale = min(useful_energy, declared_sale)
        low_price_sale = max(0.0, useful_energy - actual_high_sale)
        exchange_revenue = actual_high_sale * sell_price
        gp_sale_revenue = low_price_sale * gp_price
        gp_purchase = max(0.0, -net_root)
        purchase_cost = gp_purchase * buy_price
        balancing_penalty = shortfall * balancing_price

        totals.market_revenue += exchange_revenue + gp_sale_revenue
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

    reserve_credit = soc * _as_float(
        (config.get("storage") or {}).get("reserve_credit_per_mw_tick", 0.35),
        0.35,
    )
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
        notes.append(
            "Stage A pre-auction valuation содержит блокирующие ошибки топологии; точное проектирование "
            "переносится в post-auction planning."
        )
    return SimulationResult(
        totals=totals,
        topology=topology,
        market_bids=market_events,
        tick_rows=tick_rows,
        deficit_ticks=deficit_ticks,
        surplus_ticks=surplus_ticks,
        notes=notes,
    )


class UnifiedLotOptimizer:
    def __init__(
        self,
        *,
        base_objects: Sequence[EnergyObject],
        forecast_pack: Dict[str, Dict[str, Dict[int, float]]] | None,
        ruleset_config: Dict[str, Any] | None,
        follow_up_candidates: Sequence[Tuple[int, str, Sequence[EnergyObject]]] | None = None,
    ) -> None:
        self.config = ies2026_config(ruleset_config)
        self.dataset = dataset_from_pack(forecast_pack, config=self.config)
        self.base_objects = _clone_objects(base_objects)
        self.follow_up_candidates = list(follow_up_candidates or [])
        self.wind_valuator = WindAuctionValuator(config=self.config, dataset=self.dataset)

    def _simulate_bundle(
        self,
        *,
        objects: Sequence[EnergyObject],
        scenario_label: str,
        wind_mode: str = "robust",
    ) -> SimulationResult:
        return _simulate(
            objects=objects,
            dataset=self.dataset,
            config=self.config,
            scenario_label=scenario_label,
            wind_mode=wind_mode,
            wind_valuator=self.wind_valuator,
        )

    def _lot_wind_summary(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
        mode_deltas: Dict[str, float],
        downside_expected_value: float,
    ) -> WindPosteriorSummary:
        wind_objects = [
            obj
            for obj in candidate_objects
            if obj.category == "generator" and _norm(obj.code) == "wind"
        ]
        if not wind_objects:
            return WindPosteriorSummary(
                posterior_mean_value=round(float(mode_deltas.get("mean", 0.0)), 4),
                posterior_q25_value=round(float(mode_deltas.get("q25", 0.0)), 4),
                posterior_q10_value=round(float(mode_deltas.get("q10", 0.0)), 4),
                cvar_value=round(float(mode_deltas.get("cvar", 0.0)), 4),
                downside_expected_value=round(float(downside_expected_value), 4),
                uncertainty_penalty=round(
                    max(0.0, float(mode_deltas.get("mean", 0.0)) - float(downside_expected_value)),
                    4,
                ),
                notes=["Лот не содержит ВЭС; wind posterior fields возвращены для единообразия API."],
            )
        confidence: Dict[str, Dict[str, float]] = {}
        notes: List[str] = []
        sample_rows: List[WindPosteriorSample] = []
        for obj in wind_objects:
            posterior = self.wind_valuator.posterior_summary(obj)
            sample_rows.extend(list(posterior.samples))
            notes.extend(list(posterior.notes))
            for name, interval in posterior.confidence_by_parameter.items():
                bucket = confidence.setdefault(name, {"low": 0.0, "mid": 0.0, "high": 0.0, "_count": 0.0})
                bucket["low"] += _as_float(interval.get("low", 0.0), 0.0)
                bucket["mid"] += _as_float(interval.get("mid", 0.0), 0.0)
                bucket["high"] += _as_float(interval.get("high", 0.0), 0.0)
                bucket["_count"] += 1.0
        for bucket in confidence.values():
            count = max(1.0, float(bucket.pop("_count", 1.0)))
            bucket["low"] = round(bucket["low"] / count, 4)
            bucket["mid"] = round(bucket["mid"] / count, 4)
            bucket["high"] = round(bucket["high"] / count, 4)
        mean_value = float(mode_deltas.get("mean", 0.0))
        q25_value = float(mode_deltas.get("q25", mean_value))
        q10_value = float(mode_deltas.get("q10", q25_value))
        cvar_value = float(mode_deltas.get("cvar", q10_value))
        penalty = max(0.0, mean_value - float(downside_expected_value))
        unique_notes: List[str] = []
        for note in notes:
            if note not in unique_notes:
                unique_notes.append(note)
        unique_notes.append(
            "Auction valuation uses downside-aware posterior delta-profit instead of optimistic mean-only wind value."
        )
        return WindPosteriorSummary(
            posterior_mean_value=round(mean_value, 4),
            posterior_q25_value=round(q25_value, 4),
            posterior_q10_value=round(q10_value, 4),
            cvar_value=round(cvar_value, 4),
            downside_expected_value=round(float(downside_expected_value), 4),
            uncertainty_penalty=round(penalty, 4),
            confidence_by_parameter=confidence,
            samples=sample_rows,
            notes=unique_notes,
        )

    def _direct_delta(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
    ) -> Dict[str, Any]:
        base_only = _clone_objects(self.base_objects)
        planned_objects, planning = plan_network(
            existing_objects=base_only,
            candidate_objects=candidate_objects,
        )
        robust_results: Dict[str, Tuple[SimulationResult, SimulationResult]] = {}
        scenarios: Dict[str, ScenarioReport] = {}
        for label in ("worst", "base", "best"):
            baseline = self._simulate_bundle(objects=base_only, scenario_label=label, wind_mode="robust")
            candidate = self._simulate_bundle(objects=planned_objects, scenario_label=label, wind_mode="robust")
            robust_results[label] = (baseline, candidate)
            scenarios[label] = ScenarioReport(
                label=label,
                delta_profit=round(candidate.totals.direct_profit - baseline.totals.direct_profit, 4),
                totals=deepcopy(candidate.totals),
                notes=list(candidate.notes),
            )
        mode_deltas: Dict[str, float] = {}
        for mode in ("mean", "q25", "q10", "cvar"):
            baseline = self._simulate_bundle(objects=base_only, scenario_label="base", wind_mode=mode)
            candidate = self._simulate_bundle(objects=planned_objects, scenario_label="base", wind_mode=mode)
            mode_deltas[mode] = round(candidate.totals.direct_profit - baseline.totals.direct_profit, 4)
        robust_weights = dict(
            ((self.config.get("generation") or {}).get("wind") or {}).get("prior", {})
            .get("robust_value_weights", {})
            or {}
        )
        downside_expected = (
            float(mode_deltas.get("q25", 0.0)) * _as_float(robust_weights.get("q25", 0.5), 0.5)
            + float(mode_deltas.get("mean", 0.0)) * _as_float(robust_weights.get("mean", 0.3), 0.3)
            + float(mode_deltas.get("q10", 0.0)) * _as_float(robust_weights.get("q10", 0.2), 0.2)
        )
        expected = round(
            scenarios["base"].delta_profit * 0.55
            + scenarios["worst"].delta_profit * 0.25
            + scenarios["best"].delta_profit * 0.20,
            4,
        )
        base_result = robust_results["base"][1]
        topology = planning
        topology_score = min(
            1.0,
            len([issue for issue in topology.issues if issue.severity == "critical"]) * 0.45
            + len([issue for issue in topology.issues if issue.severity == "warning"]) * 0.12,
        )
        market_spread = abs(scenarios["best"].delta_profit - scenarios["worst"].delta_profit) / max(
            1.0,
            abs(expected) + 1.0,
        )
        balancing_score = float(base_result.totals.balancing_penalty) / max(
            1.0,
            abs(base_result.totals.direct_profit) + 1.0,
        )
        loss_score = float(base_result.totals.loss_cost) / max(
            1.0,
            abs(base_result.totals.direct_profit) + 1.0,
        )
        optimizer_cfg = dict(self.config.get("optimizer") or {})
        topology_block_penalty = _as_float(
            (self.config.get("network") or {}).get("topology_block_penalty", 120.0),
            120.0,
        )
        wind_penalty = max(0.0, float(mode_deltas.get("mean", 0.0)) - float(downside_expected))
        volatility_penalty = abs(scenarios["best"].delta_profit - scenarios["worst"].delta_profit) * _as_float(
            optimizer_cfg.get("scenario_volatility_lambda", 0.12),
            0.12,
        )
        risk_adjusted = (
            expected
            - wind_penalty * _as_float(optimizer_cfg.get("wind_uncertainty_lambda", 1.0), 1.0)
            - market_spread * abs(expected) * _as_float(optimizer_cfg.get("market_risk_lambda", 0.08), 0.08)
            - balancing_score * abs(expected) * _as_float(optimizer_cfg.get("balancing_risk_lambda", 0.08), 0.08)
            - loss_score * abs(expected) * _as_float(optimizer_cfg.get("loss_risk_lambda", 0.06), 0.06)
            - volatility_penalty
        )
        if topology.blocking:
            risk_adjusted -= topology_block_penalty
        else:
            risk_adjusted -= topology_score * topology_block_penalty * 0.25
        wind_posterior = self._lot_wind_summary(
            candidate_objects=candidate_objects,
            mode_deltas=mode_deltas,
            downside_expected_value=downside_expected,
        )
        return {
            "expected_delta_profit": round(expected, 4),
            "risk_adjusted_profit": round(risk_adjusted, 4),
            "base_result": base_result,
            "baseline_result": robust_results["base"][0],
            "topology": topology,
            "scenarios": scenarios,
            "wind_posterior": wind_posterior,
            "wind_uncertainty_penalty": round(float(wind_posterior.uncertainty_penalty), 4),
            "topology_score": round(topology_score, 4),
            "market_score": round(market_spread, 4),
            "balancing_score": round(balancing_score, 4),
            "loss_score": round(loss_score, 4),
            "mode_deltas": mode_deltas,
        }

    def _bundle_synergy_value(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
        direct_delta_profit: float,
    ) -> float:
        if len(candidate_objects) <= 1:
            return 0.0
        standalone = 0.0
        for obj in candidate_objects:
            standalone += float(
                self._direct_delta(candidate_objects=[deepcopy(obj)])["expected_delta_profit"]
            )
        return round(float(direct_delta_profit) - standalone, 4)

    def _estimate_enabler_value(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
    ) -> Tuple[float, List[str]]:
        if not any(obj.category == "infrastructure" for obj in candidate_objects):
            return 0.0, []
        unlocks: List[Tuple[float, str]] = []
        for follow_up_id, follow_up_name, follow_up_objects in self.follow_up_candidates:
            without_candidate = self._direct_delta(candidate_objects=follow_up_objects)
            with_candidate = UnifiedLotOptimizer(
                base_objects=[*self.base_objects, *_clone_objects(candidate_objects)],
                forecast_pack=None,
                ruleset_config=self.config,
                follow_up_candidates=[],
            )
            with_candidate.dataset = self.dataset
            with_candidate.wind_valuator = self.wind_valuator
            with_candidate.config = self.config
            with_candidate.base_objects = [*self.base_objects, *_clone_objects(candidate_objects)]
            gain = float(
                with_candidate._direct_delta(candidate_objects=follow_up_objects)["risk_adjusted_profit"]
                - without_candidate["risk_adjusted_profit"]
            )
            if gain <= 0.0:
                continue
            if not without_candidate["topology"].blocking and gain < 2.0:
                continue
            unlocks.append(
                (gain, f"Инфраструктура открывает лот «{follow_up_name}» (+{gain:.2f} risk-adjusted).")
            )
        unlocks.sort(key=lambda item: item[0], reverse=True)
        selected = unlocks[:3]
        return round(sum(item[0] for item in selected), 4), [item[1] for item in selected]

    def _consumer_tariff_break_even(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
        current_direct: Dict[str, Any],
    ) -> float:
        direction: AuctionDirection = "descending_consumer_tariff"
        current_tariff = _candidate_tariff_total(candidate_objects, direction)
        zero_candidates = _clone_objects(candidate_objects)
        _set_candidate_tariff(zero_candidates, 0.0, direction)
        zero_direct = self._direct_delta(candidate_objects=zero_candidates)
        current_score = float(current_direct["risk_adjusted_profit"])
        zero_score = float(zero_direct["risk_adjusted_profit"])
        if abs(current_tariff) <= 1e-9:
            return 0.0 if current_score >= 0.0 else round(max(0.0, -zero_score), 4)
        slope = (current_score - zero_score) / max(current_tariff, 1e-6)
        if slope <= 1e-9:
            return round(max(0.0, current_tariff), 4)
        break_even = max(0.0, -zero_score / slope)
        return round(float(break_even), 4)

    def _provider_tariff_break_even(
        self,
        *,
        candidate_objects: Sequence[EnergyObject],
        risk_adjusted_profit: float,
    ) -> float:
        horizon = int((self.config.get("time") or {}).get("horizon_ticks", 48) or 48)
        current = _candidate_tariff_total(candidate_objects, "ascending_service_tariff")
        return round(max(0.0, current + risk_adjusted_profit / max(1, horizon)), 4)

    def evaluate_candidate_bundle(
        self,
        *,
        lot_id: int,
        lot_name: str,
        candidate_objects: Sequence[EnergyObject],
    ) -> LotEvaluation:
        candidate = _clone_objects(candidate_objects)
        direction = _auction_direction(candidate)
        lot_profile = _lot_profile(candidate)
        direct = self._direct_delta(candidate_objects=candidate)
        direct_delta = float(direct["expected_delta_profit"])
        bundle_synergy = self._bundle_synergy_value(
            candidate_objects=candidate,
            direct_delta_profit=direct_delta,
        )
        enabler_value, enabler_notes = self._estimate_enabler_value(candidate_objects=candidate)
        expected_profit = round(direct_delta + enabler_value, 4)
        risk_adjusted_profit = round(float(direct["risk_adjusted_profit"]) + enabler_value * 0.85, 4)
        current_tariff = _candidate_tariff_total(candidate, direction)
        auction_cfg = dict(self.config.get("auction") or {})
        repeat_increment = _as_float(auction_cfg.get("repeat_increment", 0.1), 0.1)
        if direction == "descending_consumer_tariff":
            break_even = self._consumer_tariff_break_even(
                candidate_objects=candidate,
                current_direct=direct,
            )
            competitiveness = _as_float(auction_cfg.get("consumer_competitiveness_share", 0.45), 0.45)
            optimal_price = max(
                break_even,
                current_tariff - max(0.0, current_tariff - break_even) * competitiveness,
            )
            aggressive_floor = max(
                break_even,
                current_tariff - max(0.0, current_tariff - break_even) * 0.85,
            )
            opening_bid = max(optimal_price, current_tariff)
            counter_bid = max(break_even, optimal_price - repeat_increment)
            hard_limit = break_even
            price_role = "consumer_floor"
            allpay_policy = (
                "All-Pay рассматривается только в special fixed package / tie-break режиме и "
                "не должен ломать consumer floor ниже hard_limit."
            )
        else:
            break_even = self._provider_tariff_break_even(
                candidate_objects=candidate,
                risk_adjusted_profit=risk_adjusted_profit,
            )
            margin = _as_float(auction_cfg.get("provider_margin_share", 0.18), 0.18)
            optimal_price = max(0.0, break_even * (1.0 - margin))
            aggressive_floor = None
            opening_bid = max(0.0, min(current_tariff if current_tariff > 0.0 else optimal_price, optimal_price))
            counter_bid = min(break_even, max(optimal_price, opening_bid + repeat_increment))
            hard_limit = break_even
            price_role = "service_ceiling"
            allpay_policy = (
                "All-Pay допустим только при явном special-case trigger и в пределах общего лимита 5000."
            )

        topology_report = direct["topology"]
        scenario_reports = direct["scenarios"]
        topology_feasibility = _topology_feasibility(topology_report)
        topology_score = float(direct["topology_score"])
        market_score = float(direct["market_score"])
        balancing_score = float(direct["balancing_score"])
        loss_score = float(direct["loss_score"])
        wind_uncertainty_penalty = float(direct["wind_uncertainty_penalty"])
        assumptions = [
            "Stage A pre-auction valuation оценивает robust expected delta-profit и не подменяет точную прибыль смонтированной сети.",
            "Потребители платят фиксированный тариф подключения за такт, а не demand * tariff.",
            "ВЭС оцениваются через prior/posterior по скрытым thresholds, storm shutdown, hysteresis и inertia; точные параметры не считаются известными заранее.",
            "Биржевой экспорт ограничен антидемпингом 1.2 * useful_energy_(t-1) + 10 с GP fallback для остатка.",
        ]
        if wind_uncertainty_penalty > 0.0:
            assumptions.append(
                f"Wind uncertainty penalty {wind_uncertainty_penalty:.2f} вычитает downside-risk из аукционной цены."
            )
        explanation = (
            f"Stage A auction valuation для лота считает прирост прибыли до проектирования сети. "
            f"Основная цена = {optimal_price:.2f} ({price_role}), hard_limit = {hard_limit:.2f}, "
            f"expected_profit = {expected_profit:.2f}, risk_adjusted_profit = {risk_adjusted_profit:.2f}. "
            f"Wind uncertainty penalty = {wind_uncertainty_penalty:.2f}, topology = {topology_feasibility}."
        )
        mounting_requirements = [issue.message for issue in topology_report.issues if issue.severity == "critical"]
        conflicts = [issue.message for issue in topology_report.issues if issue.severity == "warning"]
        synergy_notes = list(enabler_notes)
        if bundle_synergy != 0.0:
            synergy_notes.append(f"Bundle synergy относительно суммы одиночных оценок: {bundle_synergy:.2f}.")
        if expected_profit <= 0.0:
            conflicts.append("Текущая конфигурация не даёт положительного robust expected profit.")
        current_price_gap = 0.0
        if direction == "ascending_service_tariff":
            current_price_gap = max(0.0, current_tariff - hard_limit)
        else:
            current_price_gap = max(0.0, hard_limit - current_tariff)
        drop_candidate_score = round(
            current_price_gap + wind_uncertainty_penalty + (25.0 if topology_report.blocking else 0.0),
            4,
        )
        return LotEvaluation(
            lot_id=int(lot_id),
            lot_name=lot_name,
            auction_direction=direction,
            lot_profile=lot_profile,
            expected_delta_profit=expected_profit,
            break_even_tariff=round(break_even, 4),
            recommended_bid_or_tariff=round(optimal_price, 4),
            best_case=scenario_reports["best"],
            base_case=scenario_reports["base"],
            worst_case=scenario_reports["worst"],
            topology_risk=_risk_band(topology_score),
            market_risk=_risk_band(market_score),
            balancing_risk=_risk_band(balancing_score),
            loss_risk=_risk_band(loss_score),
            explanation=explanation,
            direct_delta_profit=direct_delta,
            enabler_value=enabler_value,
            bundle_synergy_value=bundle_synergy,
            maintenance_tariff_total=current_tariff,
            topology=topology_report,
            storage_value=deepcopy(direct["base_result"].totals.storage),
            synergy_notes=synergy_notes,
            mounting_requirements=mounting_requirements,
            conflicts=conflicts,
            assumptions=assumptions,
            minimum_acceptable_tariff=round(break_even, 4) if direction == "descending_consumer_tariff" else None,
            recommended_walkdown_tariff=round(optimal_price, 4) if direction == "descending_consumer_tariff" else None,
            aggressive_floor=round(float(aggressive_floor or break_even), 4)
            if direction == "descending_consumer_tariff"
            else None,
            hard_floor=round(break_even, 4) if direction == "descending_consumer_tariff" else None,
            maximum_acceptable_service_tariff=round(break_even, 4) if direction != "descending_consumer_tariff" else None,
            recommended_bid_ceiling=round(optimal_price, 4) if direction != "descending_consumer_tariff" else None,
            soft_ceiling=round(max(0.0, optimal_price * 0.92), 4) if direction != "descending_consumer_tariff" else None,
            hard_ceiling=round(break_even, 4) if direction != "descending_consumer_tariff" else None,
            recommended_opening_bid=round(opening_bid, 4),
            recommended_counter_bid=round(counter_bid, 4),
            hard_limit=round(hard_limit, 4),
            allpay_trigger_policy=allpay_policy,
            drop_candidate_score=drop_candidate_score,
            portfolio_substitute_group=lot_profile,
            plan_b_if_lost="Перейти к следующему набору того же профиля с меньшим topology/wind downside.",
            plan_c_if_overbid="Сбросить участие на counter bid и сохранить бюджет под второй круг и substitutes.",
            analysis_stage="pre_auction_lot_valuation",
            optimal_purchase_price=round(optimal_price, 4),
            price_role=price_role,
            expected_profit_after_purchase=expected_profit,
            risk_adjusted_profit=risk_adjusted_profit,
            synergy_value=bundle_synergy,
            infrastructure_enabler_value=enabler_value,
            topology_feasibility=topology_feasibility,
            wind_uncertainty_penalty=round(wind_uncertainty_penalty, 4),
            wind_posterior=direct["wind_posterior"],
            topology_risk_score=round(topology_score, 4),
            market_risk_score=round(market_score, 4),
            balancing_risk_score=round(balancing_score, 4),
            loss_risk_score=round(loss_score, 4),
        )


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
    optimizer = UnifiedLotOptimizer(
        base_objects=base_objects,
        forecast_pack=forecast_pack,
        ruleset_config=ruleset_config,
        follow_up_candidates=follow_up_candidates,
    )
    return optimizer.evaluate_candidate_bundle(
        lot_id=lot_id,
        lot_name=lot_name,
        candidate_objects=candidate_objects,
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
    wind_valuator = WindAuctionValuator(config=config, dataset=dataset)
    working = _clone_objects(objects)
    return _simulate(
        objects=working,
        dataset=dataset,
        config=config,
        scenario_label=scenario_label,
        declared_sales=declared_sales,
        wind_mode="robust",
        wind_valuator=wind_valuator,
    )
