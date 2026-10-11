"""Deterministic checks on downloaded data. They flag; they never repair or fill in."""

from bisect import bisect_left
from datetime import date

from .timeutil import et_day, et_time


def ohlc(rows: list) -> list:
    """Bars that cannot be real: a non-positive price, a high below or a low above the rest, negative volume."""
    problems = []
    for ts, o, h, l, c, v, *_ in rows:
        if min(o, h, l, c) <= 0:
            problems.append({"ts": ts, "problem": "non-positive price"})
        elif h < max(o, c, l) or l > min(o, c):
            problems.append({"ts": ts, "problem": "high/low do not bound open/close"})
        elif v is not None and v < 0:
            problems.append({"ts": ts, "problem": "negative volume"})
    return problems


def daily_calendar(days: list, sessions: list) -> list:
    """Trading sessions with no bar, and bars on days the market was shut."""
    have, expected = {d.isoformat() for d in days}, {s["day"] for s in sessions}
    lo, hi = (min(expected), max(expected)) if expected else ("", "")
    gaps = [{"day": d, "kind": "missing_session"} for d in sorted(expected - have)]
    gaps += [{"day": d, "kind": "not_a_session"} for d in sorted(have - expected) if lo <= d <= hi]
    return sorted(gaps, key=lambda g: g["day"])


def minutes(timestamps: list, sessions: list) -> list:
    """Regular-session minutes with no bar, counted against that day's real length."""
    gaps, stamps = [], sorted(timestamps)
    for s in sessions:
        day = date.fromisoformat(s["day"])
        open_, close = et_time(day, s["open"]), et_time(day, s["close"])
        expected = (close - open_) // 60
        found = bisect_left(stamps, close) - bisect_left(stamps, open_)
        if found < expected:
            gaps.append({"day": s["day"], "kind": "missing_minutes", "expected": expected, "found": found})
    return gaps


def outliers(rows: list, splits: dict, threshold: float = 0.2) -> list:
    """Close-to-close moves beyond the threshold that no split explains."""
    flagged, prev = [], None
    for ts, _, _, _, close, *_ in rows:
        day = et_day(ts).isoformat()
        if prev and abs(close / prev - 1) > threshold and day not in splits:
            flagged.append({"day": day, "ret": round(close / prev - 1, 4)})
        prev = close
    return flagged


def cross_source(a: dict, b: dict, tolerance: float = 0.005) -> list:
    """Days two sources both cover where their closes differ by more than the tolerance."""
    return [{"day": d, "a": a[d], "b": b[d]} for d in sorted(set(a) & set(b)) if abs(a[d] / b[d] - 1) > tolerance]
