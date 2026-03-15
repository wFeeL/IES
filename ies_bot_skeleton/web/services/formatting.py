from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def display_or_dash(value: Any, *, dash: str = "—") -> Any:
    return dash if value in (None, "") else value


def format_number(
    value: Any,
    digits: int = 2,
    *,
    dash: str = "—",
) -> str:
    number = _to_decimal(value)
    if number is None:
        return dash
    if not number.is_finite():
        return dash

    precision = max(0, int(digits))
    quant = Decimal("1").scaleb(-precision)
    rounded = number.quantize(quant, rounding=ROUND_HALF_UP)
    if rounded == Decimal("-0"):
        rounded = Decimal("0")

    if rounded == rounded.to_integral():
        return str(int(rounded))

    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_tick_range(
    tick_from: Any,
    tick_to: Any,
    *,
    periods: int | None = None,
) -> str:
    left = display_or_dash(tick_from)
    right = display_or_dash(tick_to)
    base = f"{left}–{right}"
    if periods is not None and int(periods or 0) > 0:
        return f"{base} ({int(periods)} периодов)"
    return base
