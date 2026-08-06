"""Weekly opening-hours model: is a club staffed, unstaffed, or closed right now?

Planet Fitness AU clubs are typically open 24/7 but only staffed for part of
the day, so "open" and "staffed" are two separate weekly tables. A club with
no `open` table is treated as always open, which is the common case.

The occupancy endpoint carries no hours at all, so the tables come from the
hand-maintained clubs.yaml in this directory (see club_data.py).

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Day-key aliases, so a hand-maintained table can say "weekdays: 05:00-22:00"
# instead of repeating the same line five times.
_DAY_ALIASES: dict[str, tuple[int, ...]] = {
    "daily": (0, 1, 2, 3, 4, 5, 6),
    "weekdays": (0, 1, 2, 3, 4),
    "weekends": (5, 6),
    **{name: (index,) for index, name in enumerate(WEEKDAYS)},
}

_MINUTES_PER_DAY = 24 * 60

# How far ahead next_change() will look before giving up. A club whose state
# never changes (always open, never staffed) legitimately has no next change.
_HORIZON_DAYS = 8


class Staffing(StrEnum):
    """The three states a club can be in at a given moment."""

    STAFFED = "staffed"
    UNSTAFFED = "unstaffed"
    CLOSED = "closed"


class ScheduleError(ValueError):
    """A weekly hours table is malformed."""


@dataclass(frozen=True)
class Span:
    """A time range on one weekday, as minutes from that weekday's midnight.

    `end` may exceed 1440, meaning the span runs past midnight into the
    following day ("22:00-02:00"). An end of exactly "24:00" is end-of-day
    rather than a wrap, so "05:00-24:00" does not bleed into the next day.
    """

    weekday: int
    start: int
    end: int

    def as_text(self) -> str:
        return f"{_format_clock(self.start)}-{_format_clock(self.end)}"


@dataclass(frozen=True)
class ClubSchedule:
    """One club's opening and staffing tables."""

    # None means "open 24/7" — distinct from an empty tuple, which would mean
    # "never open".
    open_spans: tuple[Span, ...] | None
    staffed_spans: tuple[Span, ...]

    def state_at(self, moment: datetime) -> Staffing:
        """Resolve the club's state at an aware datetime."""
        if self.open_spans is not None and not _covers(self.open_spans, moment):
            return Staffing.CLOSED
        if _covers(self.staffed_spans, moment):
            return Staffing.STAFFED
        return Staffing.UNSTAFFED

    def next_change(self, moment: datetime) -> datetime | None:
        """The next datetime at which state_at() returns something different.

        Returns None if nothing changes within the lookahead horizon, which
        happens for schedules that are constant (e.g. open 24/7, never
        staffed).
        """
        for _from, _to, at in self._transitions(moment):
            return at
        return None

    def next_staffing_toggle(self, moment: datetime) -> datetime | None:
        """The next time the club flips specifically staffed <-> unstaffed.

        Not "the next time Staff Status changes" — a transition through
        CLOSED (closing for the night, or opening before staff arrive)
        doesn't count. Only a direct staffed->unstaffed or unstaffed->staffed
        move does, in either direction.
        """
        for frm, to, at in self._transitions(moment):
            if {frm, to} == {Staffing.STAFFED, Staffing.UNSTAFFED}:
                return at
        return None

    def _transitions(
        self, moment: datetime
    ) -> Iterator[tuple[Staffing, Staffing, datetime]]:
        """Yield (from_state, to_state, at) for every real state change after `moment`, in order.

        "Real" excludes span edges that don't actually change state_at() —
        e.g. the seam between two back-to-back staffed windows.
        """
        previous = self.state_at(moment)
        for boundary in self._boundaries(moment):
            if boundary <= moment:
                continue
            current = self.state_at(boundary)
            if current != previous:
                yield previous, current, boundary
                previous = current

    def staffed_text_for(self, day: date) -> list[str]:
        """Today's staffed windows as "HH:MM-HH:MM" strings, for attributes."""
        return [
            span.as_text()
            for span in self.staffed_spans
            if span.weekday == day.weekday()
        ]

    def _boundaries(self, moment: datetime) -> list[datetime]:
        """Every span edge in the lookahead window, in order.

        State can only change at a span edge, so these are the only instants
        worth testing.
        """
        tz = moment.tzinfo
        first_day = moment.date() - timedelta(days=1)
        edges: set[datetime] = set()
        for spans in (self.open_spans or (), self.staffed_spans):
            for start, end in _expand(spans, first_day, _HORIZON_DAYS + 2, tz):
                edges.add(start)
                edges.add(end)
        return sorted(edges)


def parse_schedule(
    open_table: dict[str, object] | None,
    staffed_table: dict[str, object] | None,
) -> ClubSchedule:
    """Build a ClubSchedule from the two raw weekday tables in clubs.yaml.

    A missing/None `open_table` means the club is open 24/7; a missing
    `staffed_table` means it is never staffed.
    """
    return ClubSchedule(
        open_spans=None if open_table is None else _parse_table(open_table, "open"),
        staffed_spans=(
            () if staffed_table is None else _parse_table(staffed_table, "staffed")
        ),
    )


def _parse_table(table: object, field: str) -> tuple[Span, ...]:
    if not isinstance(table, dict):
        raise ScheduleError(f"{field}: expected a mapping of day to time ranges")
    spans: list[Span] = []
    for raw_day, ranges in table.items():
        day = str(raw_day).strip().lower()
        if day not in _DAY_ALIASES:
            raise ScheduleError(
                f"{field}: unknown day {raw_day!r} "
                f"(expected one of {', '.join(_DAY_ALIASES)})"
            )
        # A single range may be given unwrapped ("mon: 09:00-17:00").
        items = [ranges] if isinstance(ranges, str) else ranges
        if not isinstance(items, (list, tuple)):
            raise ScheduleError(f"{field}.{day}: expected a time range or a list")
        for weekday in _DAY_ALIASES[day]:
            for item in items:
                spans.append(_parse_span(item, weekday, f"{field}.{day}"))
    return tuple(spans)


def _parse_span(text: object, weekday: int, where: str) -> Span:
    if not isinstance(text, str) or "-" not in text:
        raise ScheduleError(f"{where}: expected \"HH:MM-HH:MM\", got {text!r}")
    raw_start, _, raw_end = text.partition("-")
    start = _parse_clock(raw_start, where)
    end = _parse_clock(raw_end, where)
    if end <= start:
        # Wraps past midnight; carry it into the following day.
        end += _MINUTES_PER_DAY
    return Span(weekday=weekday, start=start, end=end)


def _parse_clock(text: str, where: str) -> int:
    """Parse "HH:MM" to minutes from midnight. "24:00" is accepted as 1440."""
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise ScheduleError(f"{where}: expected \"HH:MM\", got {text!r}")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        raise ScheduleError(f"{where}: non-numeric time {text!r}") from None
    total = hours * 60 + minutes
    if not 0 <= total <= _MINUTES_PER_DAY:
        raise ScheduleError(f"{where}: time out of range {text!r}")
    return total


def _format_clock(minutes: int) -> str:
    """Inverse of _parse_clock, so a span round-trips back to how it was written.

    A wrapped end (stored as >1440) folds back to its next-day clock time, but
    a plain end-of-day stays "24:00" rather than becoming "00:00".
    """
    if minutes == _MINUTES_PER_DAY:
        return "24:00"
    minutes %= _MINUTES_PER_DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _covers(spans: tuple[Span, ...], moment: datetime) -> bool:
    """Whether any span contains `moment`, treating each as [start, end)."""
    # Start a day early so a span that wrapped past midnight is still in play.
    first_day = moment.date() - timedelta(days=1)
    return any(
        start <= moment < end
        for start, end in _expand(spans, first_day, 3, moment.tzinfo)
    )


def _expand(
    spans: tuple[Span, ...], first_day: date, days: int, tz: tzinfo | None
) -> list[tuple[datetime, datetime]]:
    """Project weekday spans onto concrete dates as aware datetimes."""
    out: list[tuple[datetime, datetime]] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        for span in spans:
            if span.weekday == day.weekday():
                out.append((_at(day, span.start, tz), _at(day, span.end, tz)))
    return out


def _at(day: date, minutes: int, tz: tzinfo | None) -> datetime:
    """Wall-clock `minutes` after midnight on `day`, in `tz`.

    The offset is added while naive and the zone attached afterwards, so the
    result is the intended wall-clock time even across a DST shift (adding a
    timedelta to an already-aware datetime would keep the old UTC offset).
    Times that occur twice on a fall-back day resolve to the first pass.
    """
    naive = datetime.combine(day, time.min) + timedelta(minutes=minutes)
    return naive.replace(tzinfo=tz)
