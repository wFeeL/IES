from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logging_utils import log_event
from .utils import safe_getattr

DEFAULT_METHOD_CANDIDATES: Dict[str, List[str]] = {
    "line_off": ["line_off", "lineOff", "line_off_by_id", "lineOffById"],
    "robot": ["robot", "solar_robot", "ses", "solarRobot"],
    "buy": ["buy", "exchange_buy", "market_buy"],
    "sell": ["sell", "exchange_sell", "market_sell"],
    "tps": ["tps", "tps_fuel", "thermo", "tpp", "tes"],
    "storage": ["storage", "accumulator", "battery", "accum", "energy_storage", "es"],
    "storage_charge": ["charge", "storage_charge", "accumulator_charge", "battery_charge"],
    "storage_discharge": [
        "discharge",
        "storage_discharge",
        "accumulator_discharge",
        "battery_discharge",
    ],
}

DEFAULT_CONSTANT_PATHS: Dict[str, List[str]] = {
    "wear_limit": ["config.wear", "settings.wear", "constants.wear"],
    "overload_threshold": ["config.network", "settings.network", "constants.network"],
    "wear_preempt": ["config.wear", "settings.wear", "constants.wear"],
    "loss_tax": ["config.network", "settings.network", "constants.network"],
    "instant_buy_price": ["config.market", "settings.market", "constants.market"],
    "instant_sell_price": ["config.market", "settings.market", "constants.market"],
    "external_buy_price": ["config.market", "settings.market", "constants.market"],
    "external_sell_price": ["config.market", "settings.market", "constants.market"],
    "market_max_power": ["config.market", "settings.market", "constants.market"],
    "solar_angle_min": ["config.solar", "settings.solar", "constants.solar"],
    "solar_angle_max": ["config.solar", "settings.solar", "constants.solar"],
    "solar_max_step": ["config.solar", "settings.solar", "constants.solar"],
    "tps_fuel_max": ["config.tps", "settings.tps", "constants.tps"],
    "fuel_price": ["config.tps", "settings.tps", "constants.tps"],
    "eco_tax_fuel": ["config.tps", "settings.tps", "constants.tps"],
    "period_ticks": ["config.time", "settings.time", "constants.time"],
}


@dataclass
class CompatProfile:
    season: str
    method_candidates: Dict[str, List[str]] = field(
        default_factory=lambda: dict(DEFAULT_METHOD_CANDIDATES)
    )
    storage_sign_convention: str = "pos_discharge"
    constants_paths: Dict[str, List[str]] = field(
        default_factory=lambda: dict(DEFAULT_CONSTANT_PATHS)
    )
    feature_flags: Dict[str, bool] = field(default_factory=lambda: {"storage_optional": True})
    forecast_aliases: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "wind": ["wind"],
            "solar": ["solar"],
            "consumption_base": ["load", "consumption_base"],
        }
    )
    required_operations: List[str] = field(
        default_factory=lambda: ["buy", "sell", "line_off", "robot", "tps"]
    )
    profile_file: Optional[str] = None


def _compat_root() -> Path:
    return Path(__file__).resolve().parents[1] / "compat"


def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"compat profile must be object: {path}")
    return data


def _list_profile_files() -> List[Path]:
    root = _compat_root()
    if not root.exists():
        return []
    return sorted(
        [
            p
            for p in root.iterdir()
            if p.suffix.lower() in (".yaml", ".yml", ".json") and p.is_file()
        ]
    )


def _find_profile_file(season: str) -> Optional[Path]:
    season = str(season).strip()
    for ext in (".yaml", ".yml", ".json"):
        p = _compat_root() / f"{season}{ext}"
        if p.exists():
            return p
    return None


def load_compat_profile(season: str) -> CompatProfile:
    file_path = _find_profile_file(season)
    if file_path is None:
        if season != "default":
            return load_compat_profile("default")
        return CompatProfile(season="default")

    raw = _load_yaml_or_json(file_path)
    methods = dict(DEFAULT_METHOD_CANDIDATES)
    methods.update({k: list(v or []) for k, v in (raw.get("method_candidates", {}) or {}).items()})

    constants_paths = dict(DEFAULT_CONSTANT_PATHS)
    constants_paths.update(
        {k: list(v or []) for k, v in (raw.get("constants_paths", {}) or {}).items()}
    )

    feature_flags = {"storage_optional": True}
    feature_flags.update({k: bool(v) for k, v in (raw.get("feature_flags", {}) or {}).items()})

    forecast_aliases = {
        "wind": ["wind"],
        "solar": ["solar"],
        "consumption_base": ["load", "consumption_base"],
    }
    forecast_aliases.update(
        {k: list(v or []) for k, v in (raw.get("forecast_aliases", {}) or {}).items()}
    )

    return CompatProfile(
        season=str(raw.get("season", season)),
        method_candidates=methods,
        storage_sign_convention=str(raw.get("storage_sign_convention", "pos_discharge")),
        constants_paths=constants_paths,
        feature_flags=feature_flags,
        forecast_aliases=forecast_aliases,
        required_operations=list(
            raw.get("required_operations", ["buy", "sell", "line_off", "robot", "tps"])
        ),
        profile_file=str(file_path),
    )


def _has_callable(obj: Any, *names: str) -> bool:
    for name in names:
        fn = safe_getattr(obj, name, None)
        if callable(fn):
            return True
    return False


def detect_season(psm: Any) -> str:
    orders = safe_getattr(psm, "orders", None)
    if orders is None:
        return "default"
    if _has_callable(orders, "storage_charge", "storage_discharge"):
        return "2027"
    if _has_callable(orders, "lineOff", "solarRobot"):
        return "2026"
    return "default"


def environment_signature(psm: Any) -> Dict[str, Any]:
    orders = safe_getattr(psm, "orders", None)
    methods = []
    if orders is not None:
        for name in dir(orders):
            if name.startswith("_"):
                continue
            if callable(safe_getattr(orders, name, None)):
                methods.append(name)
    return {
        "order_method_count": len(methods),
        "order_methods_sample": sorted(methods)[:12],
    }


def resolve_compat_profile(
    psm: Any,
    *,
    season: Optional[str] = None,
    logger: Any = None,
) -> Tuple[CompatProfile, bool]:
    env_season = os.getenv("IES_SEASON")
    chosen = (season or env_season or "").strip()
    auto_selected = False

    if not chosen:
        chosen = detect_season(psm)
        auto_selected = True

    profile = load_compat_profile(chosen)
    if auto_selected:
        log_event(
            logger,
            "warning",
            "COMPAT_PROFILE_AUTO_SELECTED",
            season=profile.season,
            profile_file=profile.profile_file,
            signature=environment_signature(psm),
            hint="Set --season or IES_SEASON to pin profile.",
        )
    else:
        log_event(
            logger,
            "info",
            "COMPAT_PROFILE_SELECTED",
            season=profile.season,
            profile_file=profile.profile_file,
        )
    return profile, auto_selected


__all__ = [
    "CompatProfile",
    "DEFAULT_CONSTANT_PATHS",
    "DEFAULT_METHOD_CANDIDATES",
    "detect_season",
    "environment_signature",
    "load_compat_profile",
    "resolve_compat_profile",
]
