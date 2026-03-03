# constants.py
from __future__ import annotations

"""=== НАСТРОЙКИ ПЕРЕД ЗАПУСКОМ ===

1) ADAPTIVE_CONSTANTS:
   True  — пытаться брать константы из движка (psm.config/settings/...)
   False — использовать только дефолты ниже

2) PERIOD_TICKS_FALLBACK:
   Если движок не даёт период (обычно 48), используем это значение

3) STORAGE_SIGN_CONVENTION (если есть команда вида storage(id, power)):
   - "auto"         : угадать по имени
   - "pos_discharge": power>0 разряд, power<0 заряд
   - "pos_charge"   : power>0 заряд, power<0 разряд

4) TPS:
   TPS_FUEL_MAX_FALLBACK — максимум fuel (если движок не сообщил)
   TPS_ETA_NOMINAL       — грубый КПД для перевода дефицита мощности -> fuel

5) Аккумуляторы:
   ENABLE_STORAGE
   STORAGE_DISCHARGE_RESERVE_FRACTION — доля заряда в резерве
"""

ADAPTIVE_CONSTANTS: bool = True
PERIOD_TICKS_FALLBACK: int = 48
DEBUG_PRINT_ONCE: bool = True

SOLAR_EPSILON: float = 0.08
SOLAR_EXPLORE_ANGLES = [0, 25, 50, 75, 100, 124]

WEAR_PREEMPT_MARGIN_FALLBACK: float = 2.0

TPS_FUEL_MAX_FALLBACK: float = 20.0
TPS_ETA_NOMINAL: float = 0.40

ENABLE_STORAGE: bool = True
STORAGE_SIGN_CONVENTION: str = "auto"  # "auto" | "pos_discharge" | "pos_charge"
STORAGE_DISCHARGE_RESERVE_FRACTION: float = 0.15
STORAGE_CHARGE_RESERVE_FRACTION: float = 0.05
ENDGAME_FULL_DISCHARGE_TICKS: int = 2
ENDGAME_SOFT_DISCHARGE_TICKS: int = 6
ENDGAME_MEDIUM_DISCHARGE_TICKS: int = 12

ENABLE_MARKET_BUY: bool = True
ENABLE_MARKET_SELL: bool = True
BUY_INSURE_FRACTION: float = 0.25
SELL_FRACTION: float = 0.20
SURPLUS_SELL_THRESHOLD: float = 5.0
MARKET_URGENT_DEFICIT_THRESHOLD: float = 0.5

MAX_ABS_ORDER_POWER_FALLBACK: float = 60.0
