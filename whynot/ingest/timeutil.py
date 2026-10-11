"""Timestamps are whole seconds since 1970 in UTC. Days are New York calendar days."""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def parse_ts(text: str) -> int:
    """An RFC 3339 time from a provider, e.g. 2024-03-04T14:30:00Z."""
    return epoch(datetime.fromisoformat(text.replace("Z", "+00:00")))


def et_day(ts: int) -> date:
    return datetime.fromtimestamp(ts, ET).date()


def et_time(day: date, hhmm: str) -> int:
    return epoch(datetime.combine(day, time(int(hhmm[:2]), int(hhmm[3:5])), ET))


def daily_available_at(cat, day: date) -> int:
    """A day's candle is known at that session's close (13:00 on a half day). A day the
    calendar does not list is given the ordinary 16:00 close."""
    return et_time(day, cat.session_close(day) or "16:00")


def next_weekday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def occ_parse(symbol: str) -> tuple:
    """SPY240315C00500000 -> ("SPY", "2024-03-15", "call", 500.0)."""
    m = _OCC.match(symbol)
    if not m:
        raise ValueError(f"not an option symbol: {symbol!r}")
    root, yy, mm, dd, cp, strike = m.groups()
    return root, f"20{yy}-{mm}-{dd}", "call" if cp == "C" else "put", int(strike) / 1000.0


def iso(ts: int | None) -> str:
    return "-" if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
