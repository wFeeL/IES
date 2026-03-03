from __future__ import annotations

import argparse
from typing import Any, Dict, List, Sequence

from . import constants
from .adaptive import default_game_constants, get_constants_from_engine
from .adapters import OrdersAdapter, Result
from .capabilities import discover_capabilities
from .compat import resolve_compat_profile
from .controllers_balance import balance_controller
from .controllers_solar import apply_solar, solar_controller, update_solar_learning
from .controllers_wear import apply_wear_actions, wear_triggers
from .forecasts import load_forecasts
from .logging_utils import get_logger, log_event, project_version
from .models import calibrate_wind_k, obj_type
from .state import load_state, save_state
from .utils import as_float, current_tick, safe_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ies-online",
        description="Run IES online controller (ips-backed).",
    )
    parser.add_argument("--season", default=None, help="Compat season profile (e.g. 2026).")
    parser.add_argument("--strict", action="store_true", help="Fail fast on critical incompatibilities.")
    parser.add_argument("--dry-run", action="store_true", help="Probe API compatibility without sending orders.")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARNING/ERROR")
    return parser.parse_args(argv)


def _snapshot(psm: Any) -> Dict[str, float]:
    gen = 0.0
    load = 0.0
    for obj in getattr(psm, "objects", []) or []:
        t = obj_type(obj)
        g = max(0.0, as_float(safe_path(obj, "power.now.generated", 0.0), 0.0))
        c = max(0.0, as_float(safe_path(obj, "power.now.consumed", 0.0), 0.0))
        if t in ("wind", "solar", "solarrobot", "tps", "storage"):
            gen += g
        if t in ("house", "housea", "houseb", "office", "factory", "consumer"):
            load += c
    return {"generation": gen, "load": load, "net": gen - load}


def _result_stats(results: List[Result]) -> Dict[str, int]:
    return {
        "decided": len(results),
        "applied": sum(1 for r in results if r.applied),
        "failed": sum(1 for r in results if not r.ok),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = get_logger(level=args.log_level)

    try:
        import ips
    except ModuleNotFoundError as exc:
        raise RuntimeError("Module 'ips' is not installed. Bot mode requires the stand API package.") from exc

    psm = ips.init()
    profile, _auto = resolve_compat_profile(psm, season=args.season, logger=logger)
    capabilities = discover_capabilities(psm, profile, logger=logger)

    log_event(
        logger,
        "info",
        "ONLINE_START",
        project_version=project_version(),
        season=profile.season,
        strict=bool(args.strict),
        dry_run=bool(args.dry_run),
        capabilities=capabilities.summary(),
    )

    st = load_state(season=profile.season, logger=logger)
    st.season = profile.season

    forecasts = load_forecasts(psm)

    if constants.ADAPTIVE_CONSTANTS:
        gc = get_constants_from_engine(
            psm,
            period_ticks_fallback=constants.PERIOD_TICKS_FALLBACK,
            wear_preempt_margin_fallback=constants.WEAR_PREEMPT_MARGIN_FALLBACK,
            tps_fuel_max_fallback=constants.TPS_FUEL_MAX_FALLBACK,
            compat_profile=profile,
            logger=logger,
        )
    else:
        gc = default_game_constants(
            period_ticks_fallback=constants.PERIOD_TICKS_FALLBACK,
            wear_preempt_margin_fallback=constants.WEAR_PREEMPT_MARGIN_FALLBACK,
            tps_fuel_max_fallback=constants.TPS_FUEL_MAX_FALLBACK,
        )

    adapter = OrdersAdapter(
        psm,
        compat_profile=profile,
        capabilities=capabilities,
        strict=bool(args.strict),
        dry_run=bool(args.dry_run),
        logger=logger,
        storage_sign_convention=constants.STORAGE_SIGN_CONVENTION,
    )

    if constants.DEBUG_PRINT_ONCE and not st.printed_once:
        log_event(logger, "info", "ONLINE_DEBUG_DETECTED", detected_methods=adapter.detected.__dict__)
        log_event(
            logger,
            "info",
            "ONLINE_DEBUG_CONSTANTS",
            constants={
                "wear_limit": gc.wear_limit,
                "wear_preempt": gc.wear_preempt,
                "market_max_power": gc.market_max_power,
                "period_ticks": gc.period_ticks,
            },
        )
        st.printed_once = True

    update_solar_learning(psm, st, gc)
    calibrate_wind_k(psm, forecasts, st.wind_k)

    actions = wear_triggers(psm, gc)
    wear_results = apply_wear_actions(adapter, actions)

    s_cmds = solar_controller(psm, forecasts, st, gc)
    solar_results = apply_solar(adapter, s_cmds)

    balance_results = balance_controller(psm, forecasts, st, gc, adapter)

    all_results = list(wear_results) + list(solar_results) + list(balance_results)
    snap = _snapshot(psm)
    stats = _result_stats(all_results)
    log_event(
        logger,
        "info",
        "TICK_SUMMARY",
        tick=current_tick(psm),
        net_load=snap["load"],
        generation=snap["generation"],
        actions_decided=stats["decided"],
        actions_applied=stats["applied"],
        actions_failed=stats["failed"],
    )

    save_state(st)
    psm.save_and_exit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
