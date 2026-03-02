# main.py
from __future__ import annotations

import constants
from adaptive import default_game_constants, get_constants_from_engine
from adapters import OrdersAdapter
from controllers_wear import wear_triggers, apply_wear_actions
from controllers_solar import update_solar_learning, solar_controller, apply_solar
from controllers_balance import balance_controller
from forecasts import load_forecasts
from models import calibrate_wind_k
from state import load_state, save_state

def main() -> None:
    try:
        import ips
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Module 'ips' is not installed. Bot mode requires the stand API package."
        ) from exc

    psm = ips.init()
    st = load_state()

    forecasts = load_forecasts(psm)

    if constants.ADAPTIVE_CONSTANTS:
        gc = get_constants_from_engine(
            psm,
            period_ticks_fallback=constants.PERIOD_TICKS_FALLBACK,
            wear_preempt_margin_fallback=constants.WEAR_PREEMPT_MARGIN_FALLBACK,
            tps_fuel_max_fallback=constants.TPS_FUEL_MAX_FALLBACK,
        )
    else:
        gc = default_game_constants(
            period_ticks_fallback=constants.PERIOD_TICKS_FALLBACK,
            wear_preempt_margin_fallback=constants.WEAR_PREEMPT_MARGIN_FALLBACK,
            tps_fuel_max_fallback=constants.TPS_FUEL_MAX_FALLBACK,
        )

    adapter = OrdersAdapter(psm, storage_sign_convention=constants.STORAGE_SIGN_CONVENTION)

    if constants.DEBUG_PRINT_ONCE and not st.printed_once:
        print("Detected orders:", adapter.detected)
        print("GameConst:", gc)
        st.printed_once = True

    update_solar_learning(psm, st, gc)
    calibrate_wind_k(psm, forecasts, st.wind_k)

    actions = wear_triggers(psm, gc)
    apply_wear_actions(adapter, actions)

    s_cmds = solar_controller(psm, forecasts, st, gc)
    apply_solar(adapter, s_cmds)

    balance_controller(psm, forecasts, st, gc, adapter)

    save_state(st)
    psm.save_and_exit()

if __name__ == "__main__":
    main()
