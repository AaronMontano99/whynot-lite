"""The chronological split of history. Dates only; nothing is ever shuffled.

DEVELOPMENT is for building features, models and strategies. VALIDATION chooses among
development candidates. LOCKED_HOLDOUT is the final independent test: its results are
not shown to the researcher during development, and every look at it is recorded.
LIVE_PAPER is whatever arrives after deployment.

Real option prices exist only from January 2024, so the validation period is drawn to
include 2024 and the holdout to be entirely inside the period with real option data.
These dates were proposed by Claude on 2026-10-07 and are the owner's to change, but
only before any strategy has been tested against them."""

from datetime import date

DEVELOPMENT_END = date(2019, 12, 31)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_END = date(2026, 10, 7)          # the last day of history when the project restarted


def partition_of(day: date) -> str:
    if day <= DEVELOPMENT_END:
        return "DEVELOPMENT"
    if day <= VALIDATION_END:
        return "VALIDATION"
    if day <= HOLDOUT_END:
        return "LOCKED_HOLDOUT"
    return "LIVE_PAPER"
