"""Arrival-reconstruction model for estimated club occupancy.

The portal's UsersCountCurrentlyInClub is not a headcount: each member scan
adds 1, and a fixed timer (counter_window, 2h) removes it later regardless of
when the member actually left. The raw value is therefore a trailing sum of
arrivals over counter_window and carries no dwell information. Real dwell is
closer to real_dwell (1h), so this model inverts the counter — recovering
per-poll arrivals from counter deltas plus the arrivals that just aged out —
and re-sums the arrival flow over the shorter real-dwell window.

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ClubEstimate:
    """One club's estimate snapshot for a single poll."""

    estimated_occupancy: int
    raw_count: int
    warming_up: bool


class OccupancyEstimator:
    """Per-club arrival history and occupancy estimate.

    History is a mapping of wall-clock-aligned bucket timestamps to
    reconstructed arrival counts, pruned to the last counter_window. Buckets
    are keyed by timestamp (not poll sequence) so missed or delayed polls
    leave gaps instead of corrupting the model; a missing lagged bucket reads
    as 0 arrivals.
    """

    def __init__(
        self,
        counter_window: timedelta,
        real_dwell: timedelta,
        bucket_size: timedelta,
        clamp_negative: bool = True,
    ) -> None:
        if bucket_size <= timedelta(0):
            raise ValueError("bucket_size must be positive")
        if real_dwell > counter_window:
            raise ValueError("real_dwell cannot exceed counter_window")
        self._window = counter_window
        self._dwell = real_dwell
        self._bucket = bucket_size
        self._clamp = clamp_negative
        # m buckets span the counter window; the estimate reads the newest n.
        self._m = max(1, round(counter_window / bucket_size))
        self._buckets: dict[datetime, float] = {}
        self._n_prev: int | None = None
        self._last_bucket: datetime | None = None
        self._real_buckets = 0

    def _floor_to_bucket(self, now: datetime) -> datetime:
        bucket_s = self._bucket.total_seconds()
        ts = now.timestamp()
        return datetime.fromtimestamp(ts - ts % bucket_s, tz=now.tzinfo)

    def update(self, n_now: int, now: datetime) -> ClubEstimate:
        """Fold one poll's raw count into the model and return the estimate."""
        t_now = self._floor_to_bucket(now)

        if self._n_prev is None:
            # First sight of this club: assume the current counter was produced
            # by a uniform arrival flow, so the estimate starts near raw * n/m
            # instead of at zero. Real data displaces the seed over one window.
            per_bucket = n_now / self._m
            for i in range(self._m):
                self._buckets[t_now - i * self._bucket] = per_bucket
        elif t_now == self._last_bucket:
            # Second poll landing in the same bucket (delayed poll, forced
            # refresh): fold the extra delta in without re-adding the aged-out
            # bucket, which was already accounted for this bucket.
            delta = n_now - self._n_prev
            merged = self._buckets.get(t_now, 0.0) + delta
            self._buckets[t_now] = max(0.0, merged) if self._clamp else merged
        else:
            # a_now = counter delta + arrivals that just aged out of the
            # counter. Clamp guards against polling jitter / portal hiccups
            # producing negative arrivals; it slightly breaks conservation but
            # self-heals within one window.
            a_lagged = self._buckets.get(t_now - self._window, 0.0)
            a_now = (n_now - self._n_prev) + a_lagged
            if self._clamp:
                a_now = max(0.0, a_now)
            self._buckets[t_now] = a_now
            self._real_buckets += 1

        self._n_prev = n_now
        self._last_bucket = t_now

        # Prune strictly older than the window; the bucket at exactly
        # t_now - window has aged out (its arrivals were re-injected above).
        cutoff = t_now - self._window
        self._buckets = {t: a for t, a in self._buckets.items() if t > cutoff}

        dwell_cutoff = t_now - self._dwell
        estimate = sum(a for t, a in self._buckets.items() if t > dwell_cutoff)
        return ClubEstimate(
            estimated_occupancy=max(0, round(estimate)),
            raw_count=n_now,
            warming_up=self._real_buckets < self._m,
        )
