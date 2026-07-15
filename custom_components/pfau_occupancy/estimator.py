"""Static-reduction model for estimated club occupancy.

The portal's UsersCountCurrentlyInClub is not a headcount: each member scan
adds 1, and the portal itself removes it later on a fixed timer, regardless
of when the member actually left. That removal is baked into the same signal
as new arrivals, so there's no reliable way to invert it and recover an
authoritative arrival count — any attempt to reconstruct arrivals from the
counter is poisoned by the portal's own decay. Instead, real occupancy is
estimated as the reported count reduced by a fixed percentage.

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClubEstimate:
    """One club's estimate snapshot for a single poll."""

    estimated_occupancy: int
    raw_count: int


def estimate_occupancy(raw_count: int, reduction_percent: float) -> ClubEstimate:
    """Reduce the portal's reported count by a flat percentage."""
    estimated = raw_count * (1 - reduction_percent / 100)
    return ClubEstimate(
        estimated_occupancy=max(0, round(estimated)),
        raw_count=raw_count,
    )
