from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .logging_utils import log_event
from .utils import as_float, normalize_key, safe_getattr


@dataclass
class ConstantSource:
    value: float
    source_path: str
    confidence: str
    candidates: List[Tuple[str, float]] = field(default_factory=list)


@dataclass
class GameConst:
    wear_limit: float = 14.0
    wear_preempt: float = 12.0
    overload_threshold: float = 40.0
    loss_tax: float = 2.0

    penalty_factory: float = 57.0
    penalty_house_office: float = 36.0
    free_offline_house_office: int = 5

    instant_buy_price: float = 20.0
    instant_sell_price: float = 0.0
    external_buy_price: float = 10.0
    external_sell_price: float = 1.0
    market_max_power: float = 60.0

    solar_angle_min: int = 0
    solar_angle_max: int = 124
    solar_max_step: int = 20
    period_ticks: int = 48

    tps_fuel_max: float = 20.0
    fuel_price: float = 0.5
    eco_tax_fuel: float = 1.5
    constant_sources: Dict[str, ConstantSource] = field(default_factory=dict)


CONSTANT_RULES: Dict[str, Tuple[str, ...]] = {
    "wear_limit": ("wearlimit", "faillimit", "wear_limit"),
    "overload_threshold": ("overloadthreshold", "overload_threshold", "wearthreshold"),
    "wear_preempt": ("wearpreempt", "wear_preempt", "preempt"),
    "loss_tax": ("losstax", "loss_tax", "taxloss", "lossprice"),
    "instant_buy_price": ("instantbuy", "instant_buy"),
    "instant_sell_price": ("instantsell", "instant_sell"),
    "external_buy_price": ("externalbuy", "exchangeexternalbuy", "external_buy"),
    "external_sell_price": ("externalsell", "exchangeexternalsell", "external_sell"),
    "market_max_power": ("marketmaxpower", "exchangemaxpower", "market_max_power"),
    "solar_angle_min": ("solaranglemin", "solar_angle_min", "robotminangle"),
    "solar_angle_max": ("solaranglemax", "solar_angle_max", "robotmaxangle"),
    "solar_max_step": ("solarmaxstep", "solar_max_step", "robotmaxstep", "robotspeed"),
    "tps_fuel_max": ("fuelmax", "tpsfuelmax", "tps_fuel_max"),
    "fuel_price": ("fuelprice", "fuel_price"),
    "eco_tax_fuel": ("ecotaxfuel", "eco_tax_fuel", "fuel_ecotax"),
    "period_ticks": ("periodticks", "period_ticks", "dayticks", "ticksperday"),
    "penalty_factory": ("factoryrubpermw", "factory_rub_per_mw", "penalty_factory"),
    "penalty_house_office": ("class3rubpermw", "class3_rub_per_mw", "penalty_house_office"),
    "free_offline_house_office": ("class3pardon", "class3_pardon", "freeofflinehouseoffice"),
}


def default_game_constants(
    period_ticks_fallback: int,
    wear_preempt_margin_fallback: float,
    tps_fuel_max_fallback: float,
) -> GameConst:
    gc = GameConst()
    gc.period_ticks = int(period_ticks_fallback)
    gc.tps_fuel_max = float(tps_fuel_max_fallback)
    gc.wear_preempt = max(0.0, gc.wear_limit - float(wear_preempt_margin_fallback))
    return gc


def _iter_number_candidates(obj: Any, *, root: str) -> List[Tuple[str, str, float]]:
    out: List[Tuple[str, str, float]] = []
    seen = set()
    stack: List[Tuple[str, Any]] = [(root, obj)]

    while stack:
        path, cur = stack.pop()
        cur_id = id(cur)
        if cur_id in seen:
            continue
        seen.add(cur_id)

        if isinstance(cur, dict):
            for key, value in cur.items():
                child_path = f"{path}.{key}"
                key_norm = normalize_key(str(key))
                fval = as_float(value, default=float("nan"))
                if fval == fval:
                    out.append((child_path, key_norm, float(fval)))
                if isinstance(value, (dict, list, tuple)):
                    stack.append((child_path, value))
        elif isinstance(cur, (list, tuple)):
            for idx, value in enumerate(cur):
                child_path = f"{path}[{idx}]"
                fval = as_float(value, default=float("nan"))
                if fval == fval:
                    out.append((child_path, "", float(fval)))
                if isinstance(value, (dict, list, tuple)):
                    stack.append((child_path, value))
        else:
            attrs = safe_getattr(cur, "__dict__", None)
            if isinstance(attrs, dict):
                for key, value in attrs.items():
                    if str(key).startswith("_"):
                        continue
                    child_path = f"{path}.{key}"
                    key_norm = normalize_key(str(key))
                    fval = as_float(value, default=float("nan"))
                    if fval == fval:
                        out.append((child_path, key_norm, float(fval)))
                    if isinstance(value, (dict, list, tuple)):
                        stack.append((child_path, value))
    return out


def _load_runtime_override(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    over = payload.get("constants_override", {})
    if not isinstance(over, dict):
        return {}
    out: Dict[str, float] = {}
    for key, value in over.items():
        fval = as_float(value, default=float("nan"))
        if fval == fval:
            out[str(key)] = float(fval)
    return out


def _pick_candidate(
    candidates: Sequence[Tuple[str, float]],
    preferred_paths: Iterable[str],
) -> Tuple[Optional[Tuple[str, float]], str, List[Tuple[str, float]]]:
    if not candidates:
        return None, "low", []
    preferred = [normalize_key(x) for x in preferred_paths]
    ranked: List[Tuple[int, int, Tuple[str, float]]] = []
    for idx, item in enumerate(candidates):
        path, value = item
        npath = normalize_key(path)
        score = 10_000
        for pidx, pref in enumerate(preferred):
            if pref and pref in npath:
                score = pidx
                break
        ranked.append((score, idx, item))

    ranked.sort(key=lambda x: (x[0], x[1]))
    best_score = ranked[0][0]
    best_candidates = [x[2] for x in ranked if x[0] == best_score]
    best = ranked[0][2]
    if len(candidates) == 1:
        confidence = "high"
    elif len(best_candidates) == 1 and best_score < 10_000:
        confidence = "medium"
    elif len(best_candidates) == 1:
        confidence = "medium"
    else:
        confidence = "low"
    return best, confidence, list(best_candidates)


def _apply_value(gc: GameConst, name: str, value: float) -> None:
    if name in (
        "solar_angle_min",
        "solar_angle_max",
        "solar_max_step",
        "period_ticks",
        "free_offline_house_office",
    ):
        setattr(gc, name, int(value))
    else:
        setattr(gc, name, float(value))


def _record_source(
    gc: GameConst,
    name: str,
    value: float,
    path: str,
    confidence: str,
    candidates: List[Tuple[str, float]],
) -> None:
    gc.constant_sources[name] = ConstantSource(
        value=float(value),
        source_path=path,
        confidence=confidence,
        candidates=[(str(p), float(v)) for p, v in candidates],
    )


def get_constants_from_engine(
    psm: Any,
    period_ticks_fallback: int,
    wear_preempt_margin_fallback: float,
    tps_fuel_max_fallback: float,
    *,
    compat_profile: Any = None,
    logger: Any = None,
    override_path: str = "config/runtime.json",
) -> GameConst:
    gc = default_game_constants(
        period_ticks_fallback=period_ticks_fallback,
        wear_preempt_margin_fallback=wear_preempt_margin_fallback,
        tps_fuel_max_fallback=tps_fuel_max_fallback,
    )

    roots: List[Tuple[str, Any]] = []
    for name in ("config", "settings", "cfg", "game_config", "constants"):
        c = safe_getattr(psm, name, None)
        if c is not None:
            roots.append((name, c))
    roots.append(("psm", psm))

    path_priority = {}
    if compat_profile is not None:
        path_priority = dict(getattr(compat_profile, "constants_paths", {}) or {})

    for const_name, key_substrings in CONSTANT_RULES.items():
        matches: List[Tuple[str, float]] = []
        for root_name, root_obj in roots:
            for path, key_norm, val in _iter_number_candidates(root_obj, root=root_name):
                if key_norm and any(sub in key_norm for sub in key_substrings):
                    matches.append((path, val))

        chosen, confidence, ambiguous = _pick_candidate(matches, path_priority.get(const_name, []))
        if chosen is None:
            continue
        source_path, value = chosen
        _apply_value(gc, const_name, value)
        _record_source(gc, const_name, value, source_path, confidence, matches)

        if confidence == "low":
            log_event(
                logger,
                "warning",
                "ADAPTIVE_CONSTANT_AMBIGUOUS",
                constant=const_name,
                selected_source=source_path,
                selected_value=value,
                candidates=[{"path": p, "value": v} for p, v in matches],
            )
        else:
            log_event(
                logger,
                "info",
                "ADAPTIVE_CONSTANT_SELECTED",
                constant=const_name,
                value=value,
                source_path=source_path,
                confidence=confidence,
            )

    override_values = _load_runtime_override(Path(override_path))
    for key, value in override_values.items():
        if not hasattr(gc, key):
            continue
        _apply_value(gc, key, value)
        _record_source(
            gc,
            key,
            value,
            f"override:{override_path}",
            "high",
            [(f"override:{override_path}", value)],
        )
        log_event(
            logger,
            "info",
            "ADAPTIVE_CONSTANT_OVERRIDE",
            constant=key,
            value=value,
            source_path=override_path,
        )

    if gc.wear_preempt <= 0:
        gc.wear_preempt = max(0.0, gc.wear_limit - float(wear_preempt_margin_fallback))
    if gc.solar_angle_min > gc.solar_angle_max:
        gc.solar_angle_min, gc.solar_angle_max = gc.solar_angle_max, gc.solar_angle_min
    gc.solar_max_step = max(1, int(gc.solar_max_step))
    gc.wear_preempt = min(gc.wear_preempt, gc.wear_limit)
    gc.period_ticks = max(1, int(gc.period_ticks))

    return gc
