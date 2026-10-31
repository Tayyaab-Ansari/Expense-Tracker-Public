"""Business date helpers for the configured timezone."""

import calendar
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

TZ = ZoneInfo(settings.TIMEZONE)

PERIODS = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_7_days",
    "last_30_days",
    "this_year",
)

_MONTH_NAMES = {
    name.lower(): number
    for number, name in enumerate(calendar.month_name)
    if number
} | {
    name.lower(): number
    for number, name in enumerate(calendar.month_abbr)
    if number
}

_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$")
_NAMED_MONTH_PATTERN = re.compile(r"^(?P<name>[a-z]+)\.?(?:\s+(?P<year>\d{4}))?$")


def now_local() -> datetime:
    """Current time in the configured timezone."""
    return datetime.now(TZ)


def today() -> date:
    """Current business date in the configured timezone."""
    return now_local().date()


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last date of the given month."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Year and month shifted by a number of months."""
    index = (year * 12 + month - 1) + delta
    return index // 12, index % 12 + 1


def parse_date(value: str | None) -> date:
    """Parse an ISO date, defaulting to today."""
    if value is None:
        return today()
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value!r}, expected ISO format YYYY-MM-DD") from exc


def parse_month(value: str | None) -> tuple[int, int]:
    """Parse a month as (year, month) from ISO, a month name or a relative keyword."""
    current = today()
    if value is None:
        return current.year, current.month

    text = value.strip().lower()
    if text in ("this_month", "this month"):
        return current.year, current.month
    if text in ("last_month", "last month"):
        return _shift_month(current.year, current.month, -1)

    iso_match = _MONTH_PATTERN.match(text)
    if iso_match:
        month = int(iso_match["month"])
        if not 1 <= month <= 12:
            raise ValueError(f"Invalid month: {value!r}")
        return int(iso_match["year"]), month

    named_match = _NAMED_MONTH_PATTERN.match(text)
    if named_match and named_match["name"] in _MONTH_NAMES:
        month = _MONTH_NAMES[named_match["name"]]
        if named_match["year"]:
            return int(named_match["year"]), month
        year = current.year if month <= current.month else current.year - 1
        return year, month

    raise ValueError(f"Invalid month: {value!r}")


def resolve_period(
    period: str | None, start: str | None = None, end: str | None = None
) -> tuple[date, date]:
    """Resolve a named period or explicit ISO range into inclusive start and end dates."""
    if start or end:
        if not start:
            raise ValueError("start is required when end is given")
        start_date = parse_date(start)
        end_date = parse_date(end)
        if start_date > end_date:
            raise ValueError("start must not be after end")
        return start_date, end_date

    current = today()
    name = (period or "this_month").strip().lower()
    if name == "today":
        return current, current
    if name == "yesterday":
        yesterday = current - timedelta(days=1)
        return yesterday, yesterday
    if name == "this_week":
        monday = current - timedelta(days=current.weekday())
        return monday, monday + timedelta(days=6)
    if name == "last_week":
        monday = current - timedelta(days=current.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if name == "this_month":
        return month_bounds(current.year, current.month)
    if name == "last_month":
        return month_bounds(*_shift_month(current.year, current.month, -1))
    if name == "last_7_days":
        return current - timedelta(days=6), current
    if name == "last_30_days":
        return current - timedelta(days=29), current
    if name == "this_year":
        return date(current.year, 1, 1), date(current.year, 12, 31)
    raise ValueError(f"Unknown period: {period!r}, expected one of {', '.join(PERIODS)}")


def human_date(value: date) -> str:
    """Format a date as Today, Yesterday or '28 Oct', with the year when not current."""
    current = today()
    if value == current:
        return "Today"
    if value == current - timedelta(days=1):
        return "Yesterday"
    if value.year == current.year:
        return f"{value.day} {calendar.month_abbr[value.month]}"
    return f"{value.day} {calendar.month_abbr[value.month]} {value.year}"
