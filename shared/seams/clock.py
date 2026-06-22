"""Clock seam — injected wherever a deadline is computed (brief §7, §8.2, §5).

Never read the wall-clock directly. The Clock supports the 20-working-day FOIA
response clock, business-day math (skipping weekends + federal holidays),
deadline-risk status, and a separate configurable requester-response grace
window — but it holds NO tolling/grace STATE. Tolling and the grace window are
Maestro's case state; the Clock takes `tolled_days` as a parameter (spec item 7).
The clock never closes a case (§5): `deadline_status` only reports; close-out is
a human decision routed by the case model.

`jurisdiction` is a real parameter (holiday calendars differ by regime later).

Backings:
- `ManualClock` — demo: deterministic, manual `advance` for recorded demos.
- `SystemClock`  — production: real wall-clock; `advance` is a no-op/raises.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Protocol, runtime_checkable

DeadlineStatus = Literal["on_track", "at_risk", "overdue"]

# Seed: U.S. federal holidays observed by agencies, as fixed observed dates for
# the demo window 2025-06 .. 2026-12. A real backing would compute these; a
# small constant keeps business-day math deterministic and reviewable for the
# demo. Logged in ASSUMPTIONS.md.
_FEDERAL_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025 (second half)
        date(2025, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 10, 13),  # Columbus Day
        date(2025, 11, 11),  # Veterans Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # MLK Day
        date(2026, 2, 16),   # Washington's Birthday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (observed, Jul 4 = Sat)
        date(2026, 9, 7),    # Labor Day
        date(2026, 10, 12),  # Columbus Day
        date(2026, 11, 11),  # Veterans Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)

# Default requester-response grace window (§5): 30 working days. Separate from
# the 20-working-day FOIA response clock. Routes to a human close-out queue when
# it lapses — never an auto-close.
DEFAULT_GRACE_WINDOW_WORKING_DAYS = 30

# Default FOIA response clock (§5).
FOIA_RESPONSE_WORKING_DAYS = 20

# How many working days of slack before due counts as "at_risk".
_AT_RISK_THRESHOLD_WORKING_DAYS = 3


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _FEDERAL_HOLIDAYS


@runtime_checkable
class Clock(Protocol):
    """Injected deadline service (§7). Holds no tolling/grace state."""

    def now(self, jurisdiction: str) -> datetime:
        """Current time under `jurisdiction` (tz-aware, UTC)."""
        ...

    def advance(self, business_days: int) -> None:
        """Demo only: move the clock forward by `business_days` working days."""
        ...

    def add_business_days(self, start: datetime, n: int) -> datetime:
        """Pure: `start` plus `n` working days (weekends + federal holidays skipped)."""
        ...

    def deadline_status(
        self, start: datetime, due: datetime, tolled_days: int
    ) -> DeadlineStatus:
        """Report on_track | at_risk | overdue, given externally-tracked tolling."""
        ...


def _add_business_days(start: datetime, n: int) -> datetime:
    """Add `n` working days to `start`, preserving time-of-day (pure helper)."""
    if n < 0:
        raise ValueError("add_business_days expects n >= 0")
    cursor = start
    remaining = n
    while remaining > 0:
        cursor = cursor + timedelta(days=1)
        if _is_business_day(cursor.date()):
            remaining -= 1
    return cursor


def _business_days_between(start: datetime, end: datetime) -> int:
    """Count working days strictly after `start`'s date up to and incl. `end`'s date.

    Negative if `end` precedes `start`. Used to measure how much working-day
    slack remains before a due date.
    """
    sign = 1
    a, b = start.date(), end.date()
    if b < a:
        sign = -1
        a, b = b, a
    count = 0
    cursor = a
    while cursor < b:
        cursor = cursor + timedelta(days=1)
        if _is_business_day(cursor):
            count += 1
    return sign * count


class ManualClock:
    """Demo Clock: deterministic, manually advanced (§7).

    Starts at a fixed instant and only moves when `advance` is called, so a
    recorded demo can jump the 20-day clock on cue. `advance` moves by *working*
    days to match how deadlines are reasoned about.
    """

    def __init__(self, start: datetime | None = None) -> None:
        if start is None:
            start = datetime(2026, 6, 22, 9, 0, 0, tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._now = start

    def now(self, jurisdiction: str) -> datetime:
        return self._now

    def advance(self, business_days: int) -> None:
        self._now = _add_business_days(self._now, business_days)

    def add_business_days(self, start: datetime, n: int) -> datetime:
        return _add_business_days(start, n)

    def deadline_status(self, start: datetime, due: datetime, tolled_days: int) -> DeadlineStatus:
        return _deadline_status(self._now, due, tolled_days)


class SystemClock:
    """Production Clock: real wall-clock (§7).

    `advance` is a no-op (you cannot move real time); call it in demo wiring
    only. `now` ignores `start`/state — it reads the system clock. Business-day
    math and status remain pure and shared with the demo backing.
    """

    def now(self, jurisdiction: str) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, business_days: int) -> None:
        # Real time cannot be advanced; no-op so demo wiring that calls advance
        # does not crash in production. (Intentional, not a stub.)
        return None

    def add_business_days(self, start: datetime, n: int) -> datetime:
        return _add_business_days(start, n)

    def deadline_status(self, start: datetime, due: datetime, tolled_days: int) -> DeadlineStatus:
        return _deadline_status(self.now("federal_foia"), due, tolled_days)


def _deadline_status(now: datetime, due: datetime, tolled_days: int) -> DeadlineStatus:
    """Compute deadline status, shifting `due` later by `tolled_days` working days.

    `tolled_days` is supplied by Maestro (the Clock holds no tolling state). A
    positive value extends the effective due date (the clock paused during
    clarification). Status:
      overdue  — now is past the effective due date;
      at_risk  — within `_AT_RISK_THRESHOLD_WORKING_DAYS` working days of it;
      on_track — otherwise.
    """
    effective_due = _add_business_days(due, max(0, tolled_days))
    if now > effective_due:
        return "overdue"
    slack = _business_days_between(now, effective_due)
    if slack <= _AT_RISK_THRESHOLD_WORKING_DAYS:
        return "at_risk"
    return "on_track"


__all__ = [
    "Clock",
    "ManualClock",
    "SystemClock",
    "DeadlineStatus",
    "DEFAULT_GRACE_WINDOW_WORKING_DAYS",
    "FOIA_RESPONSE_WORKING_DAYS",
]
