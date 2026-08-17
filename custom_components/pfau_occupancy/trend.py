"""Which way a club's occupancy is moving: filling up, or emptying out.

Comparing the last two polls would be far too jumpy to act on. The portal's
counter is a step function — it jumps as members scan in and drops in lumps
as its own two-hour removal timer expires — so consecutive samples routinely
disagree with the direction the club is actually heading. Instead this fits a
least-squares line through every sample in a rolling window and uses its
gradient, in people per hour.

Two further guards against a flapping entity:

* A deadband. A gradient inside +/- min_gradient is treated as "no real
  movement" rather than being forced into one direction or the other.
* Hysteresis. Inside the deadband the previous direction is held rather than
  reset, so a club that stops filling stays on "getting busier" until it
  genuinely starts emptying.

There is always a direction. Before a line can be fitted — after a restart,
or for a club that has just appeared — the reading falls back to RISING
rather than going unknown, so downstream automations and templates never have
to handle an absent state. `TrendReading.established` distinguishes that
assumed direction from a measured one.

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

_SECONDS_PER_HOUR = 3600.0


class Trend(StrEnum):
    """The direction a club's occupancy is moving in."""

    RISING = "rising"
    FALLING = "falling"


# What a reading reports before it has enough data to measure anything.
DEFAULT_TREND = Trend.RISING


@dataclass(frozen=True)
class TrendSample:
    """One occupancy observation.

    `at` is elapsed seconds from any fixed origin — only differences are ever
    used, so callers should pass a monotonic clock rather than wall time and
    stay immune to NTP steps and DST shifts.
    """

    at: float
    people: int


@dataclass(frozen=True)
class TrendReading:
    """A club's direction of travel over one window."""

    direction: Trend
    gradient: float
    samples: int
    span_seconds: float
    change: int
    # False when no line could be fitted, so `direction` is the assumed
    # default rather than anything measured.
    established: bool


def measure_trend(
    samples: Sequence[TrendSample],
    min_gradient: float,
    previous: Trend = DEFAULT_TREND,
) -> TrendReading:
    """Fit a line through `samples` and classify its gradient.

    `min_gradient` is the deadband half-width in people per hour. `previous`
    is the direction currently being reported, held onto when the gradient
    falls inside the deadband — and, on the first reading, the default that
    stands in until a line can be fitted.
    """
    ordered = sorted(samples, key=lambda sample: sample.at)
    count = len(ordered)
    span = ordered[-1].at - ordered[0].at if count >= 2 else 0.0
    change = ordered[-1].people - ordered[0].people if count >= 2 else 0

    gradient = _least_squares_gradient(ordered)
    if gradient is None:
        # Not enough spread to fit anything; keep reporting whatever we were.
        return TrendReading(previous, 0.0, count, span, change, established=False)

    if gradient >= min_gradient:
        direction = Trend.RISING
    elif gradient <= -min_gradient:
        direction = Trend.FALLING
    else:
        direction = previous

    return TrendReading(
        direction, round(gradient, 2), count, span, change, established=True
    )


def within_window(
    samples: Sequence[TrendSample],
    now: float,
    window_seconds: float,
    keep_at_least: int = 2,
) -> list[TrendSample]:
    """Drop samples older than the window, keeping enough to still fit a line.

    `keep_at_least` matters when polling is slower than the window: without
    it every sample would age out between polls and the trend could never
    establish itself.
    """
    kept = [sample for sample in samples if now - sample.at <= window_seconds]
    if len(kept) < keep_at_least:
        kept = list(samples)[-keep_at_least:]
    return kept


def _least_squares_gradient(ordered: Sequence[TrendSample]) -> float | None:
    """Slope of the best-fit line, in people per hour.

    None when a line can't be fitted: fewer than two samples, or every
    sample sharing one timestamp (which would divide by zero).
    """
    count = len(ordered)
    if count < 2:
        return None

    mean_at = sum(sample.at for sample in ordered) / count
    mean_people = sum(sample.people for sample in ordered) / count

    variance = sum((sample.at - mean_at) ** 2 for sample in ordered)
    if variance == 0:
        return None

    covariance = sum(
        (sample.at - mean_at) * (sample.people - mean_people) for sample in ordered
    )
    return covariance / variance * _SECONDS_PER_HOUR
