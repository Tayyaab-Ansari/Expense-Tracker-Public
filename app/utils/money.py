"""Decimal money helpers and Rupee formatting."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENTS = Decimal("0.01")


def to_decimal(value: int | float | str | Decimal, positive: bool = False) -> Decimal:
    """Convert a number or string amount to a Decimal quantized to two places."""
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("_", "").strip()
        if not cleaned:
            raise ValueError("Invalid amount: empty string")
    else:
        cleaned = str(value)

    try:
        amount = Decimal(cleaned).quantize(CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc

    if not amount.is_finite():
        raise ValueError(f"Invalid amount: {value!r}")
    if positive and amount <= 0:
        raise ValueError(f"Amount must be greater than zero, got {value!r}")
    return amount


def format_rs(amount: int | float | str | Decimal) -> str:
    """Format an amount as 'Rs 1,500', with two decimals only when needed."""
    value = to_decimal(amount)
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    whole = absolute == absolute.to_integral_value()
    digits = f"{absolute:,.0f}" if whole else f"{absolute:,.2f}"
    return f"{sign}Rs {digits}"


def pct_change(old: int | float | str | Decimal, new: int | float | str | Decimal) -> Decimal | None:
    """Percentage change from old to new, or None when old is zero."""
    previous = to_decimal(old)
    current = to_decimal(new)
    if previous == 0:
        return None
    return ((current - previous) / previous * 100).quantize(CENTS, rounding=ROUND_HALF_UP)
