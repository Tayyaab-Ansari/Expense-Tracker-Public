"""Tests for the date and money helpers."""

from datetime import date
from decimal import Decimal

import pytest

from app.utils import dates, money


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch):
    """Freeze dates.today() at a chosen date for the duration of a test."""

    def freeze(value: date) -> date:
        monkeypatch.setattr(dates, "today", lambda: value)
        return value

    return freeze


def test_now_local_uses_configured_timezone() -> None:
    assert dates.now_local().tzinfo is dates.TZ
    assert dates.now_local().date() == dates.today()


def test_periods_match_resolve_period() -> None:
    for period in dates.PERIODS:
        start, end = dates.resolve_period(period)
        assert start <= end


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("today", (date(2026, 10, 31), date(2026, 10, 31))),
        ("yesterday", (date(2026, 10, 30), date(2026, 10, 30))),
        ("this_week", (date(2026, 10, 26), date(2026, 11, 1))),
        ("last_week", (date(2026, 10, 19), date(2026, 10, 25))),
        ("this_month", (date(2026, 10, 1), date(2026, 10, 31))),
        ("last_month", (date(2026, 9, 1), date(2026, 9, 30))),
        ("last_7_days", (date(2026, 10, 25), date(2026, 10, 31))),
        ("last_30_days", (date(2026, 10, 2), date(2026, 10, 31))),
        ("this_year", (date(2026, 1, 1), date(2026, 12, 31))),
    ],
)
def test_resolve_period_named(frozen_today, period: str, expected: tuple[date, date]) -> None:
    frozen_today(date(2026, 10, 31))
    assert dates.resolve_period(period) == expected


def test_resolve_period_defaults_to_this_month(frozen_today) -> None:
    frozen_today(date(2026, 2, 14))
    assert dates.resolve_period(None) == (date(2026, 2, 1), date(2026, 2, 28))


def test_resolve_period_week_starts_on_monday(frozen_today) -> None:
    monday = date(2026, 10, 26)
    assert monday.weekday() == 0
    frozen_today(monday)
    assert dates.resolve_period("this_week") == (monday, date(2026, 11, 1))
    assert dates.resolve_period("last_week") == (date(2026, 10, 19), date(2026, 10, 25))


def test_resolve_period_week_on_sunday(frozen_today) -> None:
    sunday = date(2026, 11, 1)
    assert sunday.weekday() == 6
    frozen_today(sunday)
    assert dates.resolve_period("this_week") == (date(2026, 10, 26), sunday)


def test_resolve_period_explicit_range_overrides_period() -> None:
    assert dates.resolve_period("today", "2026-01-05", "2026-01-09") == (
        date(2026, 1, 5),
        date(2026, 1, 9),
    )


def test_resolve_period_start_only_runs_to_today(frozen_today) -> None:
    frozen_today(date(2026, 10, 31))
    assert dates.resolve_period(None, start="2026-10-01") == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )


def test_resolve_period_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        dates.resolve_period("next_decade")
    with pytest.raises(ValueError):
        dates.resolve_period(None, end="2026-10-01")
    with pytest.raises(ValueError):
        dates.resolve_period(None, start="2026-10-05", end="2026-10-01")


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 10, (date(2026, 10, 1), date(2026, 10, 31))),
        (2026, 2, (date(2026, 2, 1), date(2026, 2, 28))),
        (2024, 2, (date(2024, 2, 1), date(2024, 2, 29))),
    ],
)
def test_month_bounds(year: int, month: int, expected: tuple[date, date]) -> None:
    assert dates.month_bounds(year, month) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, (2026, 10)),
        ("2026-10", (2026, 10)),
        ("2026-1", (2026, 1)),
        ("this_month", (2026, 10)),
        ("last_month", (2026, 9)),
        ("october", (2026, 10)),
        ("October", (2026, 10)),
        ("oct", (2026, 10)),
        ("oct 2026", (2026, 10)),
        ("December 2025", (2025, 12)),
        ("december", (2025, 12)),
        ("september", (2026, 9)),
    ],
)
def test_parse_month(frozen_today, value: str | None, expected: tuple[int, int]) -> None:
    frozen_today(date(2026, 10, 31))
    assert dates.parse_month(value) == expected


def test_parse_month_last_month_crosses_year(frozen_today) -> None:
    frozen_today(date(2026, 1, 15))
    assert dates.parse_month("last_month") == (2025, 12)


def test_parse_month_rejects_unknown(frozen_today) -> None:
    frozen_today(date(2026, 10, 31))
    for value in ("octobre", "2026-13", "13", ""):
        with pytest.raises(ValueError):
            dates.parse_month(value)


def test_parse_date(frozen_today) -> None:
    frozen_today(date(2026, 10, 31))
    assert dates.parse_date(None) == date(2026, 10, 31)
    assert dates.parse_date("2026-03-07") == date(2026, 3, 7)
    assert dates.parse_date(" 2026-03-07 ") == date(2026, 3, 7)
    with pytest.raises(ValueError):
        dates.parse_date("07/03/2026")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 31), "Today"),
        (date(2026, 10, 30), "Yesterday"),
        (date(2026, 10, 28), "28 Oct"),
        (date(2026, 1, 3), "3 Jan"),
        (date(2025, 10, 28), "28 Oct 2025"),
        (date(2027, 1, 1), "1 Jan 2027"),
    ],
)
def test_human_date(frozen_today, value: date, expected: str) -> None:
    frozen_today(date(2026, 10, 31))
    assert dates.human_date(value) == expected


def to_str(value: Decimal) -> str:
    """Render a Decimal with its two-place exponent for comparison."""
    return f"{value:.2f}"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1500, "1500.00"),
        (1500.5, "1500.50"),
        ("1,500", "1500.00"),
        ("1,500.456", "1500.46"),
        ("  2,000.5 ", "2000.50"),
        (Decimal("0.005"), "0.01"),
        (-500, "-500.00"),
        (0, "0.00"),
    ],
)
def test_to_decimal(value, expected: str) -> None:
    assert to_str(money.to_decimal(value)) == expected


def test_to_decimal_positive_flag() -> None:
    assert money.to_decimal("10", positive=True) == Decimal("10.00")
    for value in (0, "-5"):
        with pytest.raises(ValueError):
            money.to_decimal(value, positive=True)


def test_to_decimal_rejects_garbage() -> None:
    for value in ("", "abc", "12abc", "nan", "Infinity"):
        with pytest.raises(ValueError):
            money.to_decimal(value)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (1500, "Rs 1,500"),
        ("1500", "Rs 1,500"),
        (1500.5, "Rs 1,500.50"),
        (Decimal("1234567.89"), "Rs 1,234,567.89"),
        (0, "Rs 0"),
        (-500, "-Rs 500"),
        (Decimal("-500.25"), "-Rs 500.25"),
    ],
)
def test_format_rs(amount, expected: str) -> None:
    assert money.format_rs(amount) == expected


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (100, 150, Decimal("50.00")),
        (100, 50, Decimal("-50.00")),
        (100, 100, Decimal("0.00")),
        ("1,500", "2,000", Decimal("33.33")),
        (0, 500, None),
        ("0.00", 0, None),
    ],
)
def test_pct_change(old, new, expected: Decimal | None) -> None:
    assert money.pct_change(old, new) == expected
