"""Unit tests for the arrival-reconstruction occupancy estimator.

The estimator module has no Home Assistant imports, so these run under plain
pytest: `pytest tests/` from the repo root.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load estimator.py directly by path: importing it via the package would pull
# in custom_components/pfau_occupancy/__init__.py, which imports Home
# Assistant — unavailable (and unwanted) in these pure unit tests.
_spec = importlib.util.spec_from_file_location(
    "estimator",
    Path(__file__).parent.parent
    / "custom_components"
    / "pfau_occupancy"
    / "estimator.py",
)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module  # dataclass decorator needs this at exec time
_spec.loader.exec_module(_module)
OccupancyEstimator = _module.OccupancyEstimator

BUCKET = timedelta(minutes=5)
WINDOW = timedelta(minutes=120)
DWELL = timedelta(minutes=60)
M = 24  # WINDOW / BUCKET
N = 12  # DWELL / BUCKET

T0 = datetime(2026, 7, 6, 12, 0, 0)


def make_estimator(clamp: bool = True) -> OccupancyEstimator:
    return OccupancyEstimator(
        counter_window=WINDOW,
        real_dwell=DWELL,
        bucket_size=BUCKET,
        clamp_negative=clamp,
    )


class PortalCounterSim:
    """Simulates the portal's counter: each arrival adds 1 and is removed
    exactly WINDOW later. Feeding this into the estimator lets tests compare
    the reconstruction against known ground-truth arrivals."""

    def __init__(self) -> None:
        self._arrivals: list[tuple[datetime, int]] = []

    def arrive(self, t: datetime, count: int) -> None:
        self._arrivals.append((t, count))

    def count_at(self, t: datetime) -> int:
        return sum(c for (ta, c) in self._arrivals if ta <= t < ta + WINDOW)


def test_seed_conserves_raw_count() -> None:
    est = make_estimator()
    result = est.update(24, T0)
    assert sum(est._buckets.values()) == 24
    # Uniform seed: estimate = raw * n/m.
    assert result.estimated_occupancy == round(24 * N / M)
    assert result.warming_up is True
    assert result.raw_count == 24


def test_conservation_across_steps_unclamped() -> None:
    """sum(history) == raw count at every step (clamp disabled, no gaps)."""
    est = make_estimator(clamp=False)
    sim = PortalCounterSim()
    arrivals = [3, 0, 7, 2, 0, 0, 5, 1, 9, 4, 0, 2, 6, 0, 3, 8, 1, 0, 0, 4]
    t = T0
    for a in arrivals:
        sim.arrive(t, a)
        est.update(sim.count_at(t), t)
        assert sum(est._buckets.values()) == sim.count_at(t)
        t += BUCKET


def test_constant_rate_ratio() -> None:
    """A steady arrival rate yields estimate == raw * n/m after warm-up.

    The sim is pre-warmed to steady state before the estimator's first poll so
    the uniform seed matches the true history and the ratio is exact. (A cold
    sim leaves a small bounded seed residual — see the estimator docstring.)
    """
    rate = 4
    est = make_estimator()
    sim = PortalCounterSim()
    t = T0
    for _ in range(M):
        sim.arrive(t, rate)
        t += BUCKET
    t -= BUCKET  # first estimator poll sees the full steady-state counter

    result = est.update(sim.count_at(t), t)
    assert result.raw_count == rate * M
    # Seed distributes the counter uniformly, which here is exactly right.
    assert result.estimated_occupancy == rate * N

    for _ in range(2 * M):
        t += BUCKET
        sim.arrive(t, rate)
        result = est.update(sim.count_at(t), t)
    assert result.warming_up is False
    assert result.raw_count == rate * M
    assert result.estimated_occupancy == rate * N  # == raw * n/m


def test_burst_decays_at_dwell_not_window() -> None:
    """After a burst, the estimate zeroes in DWELL while raw takes WINDOW."""
    est = make_estimator()
    sim = PortalCounterSim()

    # Establish a quiet baseline so the burst lands on real (non-seed) history.
    t = T0
    for _ in range(M + 1):
        result = est.update(sim.count_at(t), t)
        t += BUCKET
    assert result.estimated_occupancy == 0

    burst_time = t
    sim.arrive(burst_time, 30)
    result = est.update(sim.count_at(t), t)
    assert result.estimated_occupancy == 30
    assert result.raw_count == 30

    # Walk forward; capture state just before and after each threshold.
    while t < burst_time + WINDOW + BUCKET:
        t += BUCKET
        result = est.update(sim.count_at(t), t)
        age = t - burst_time
        if age < DWELL:
            assert result.estimated_occupancy == 30, f"early drop at {age}"
            assert result.raw_count == 30
        elif age < WINDOW:
            assert result.estimated_occupancy == 0, f"lingering at {age}"
            assert result.raw_count == 30  # raw still holds the burst
        else:
            assert result.estimated_occupancy == 0
            assert result.raw_count == 0  # portal timer finally expired


def test_warming_up_clears_after_full_window() -> None:
    est = make_estimator()
    t = T0
    result = est.update(10, t)
    assert result.warming_up is True
    for _ in range(M):
        t += BUCKET
        result = est.update(10, t)
    assert result.warming_up is False


def test_missed_polls_dip_then_self_heal() -> None:
    """Skipped polls leave gap buckets that read 0, dipping the estimate by
    the unobserved arrivals; the dip self-heals once the gap ages past the
    dwell window. Buckets are timestamp-keyed, so the gap must not shift or
    corrupt the rest of the history."""
    rate = 2
    gap = 3
    est = make_estimator()
    sim = PortalCounterSim()

    # Pre-warm the sim, then run one full window of real polls.
    t = T0
    for _ in range(M):
        sim.arrive(t, rate)
        t += BUCKET
    t -= BUCKET
    est.update(sim.count_at(t), t)
    for _ in range(M):
        t += BUCKET
        sim.arrive(t, rate)
        result = est.update(sim.count_at(t), t)
    assert result.estimated_occupancy == rate * N

    # Members keep arriving but the coordinator misses `gap` polls.
    for _ in range(gap):
        t += BUCKET
        sim.arrive(t, rate)
    t += BUCKET
    sim.arrive(t, rate)
    result = est.update(sim.count_at(t), t)
    # Raw is unaffected; the estimate dips by exactly the gap's arrivals.
    assert result.raw_count == rate * M
    assert result.estimated_occupancy == rate * (N - gap)

    # Once the gap buckets age out of the dwell window, steady state returns.
    for _ in range(N):
        t += BUCKET
        sim.arrive(t, rate)
        result = est.update(sim.count_at(t), t)
    assert result.estimated_occupancy == rate * N


def test_negative_delta_clamped() -> None:
    """A portal hiccup shrinking the counter must not produce negative state."""
    est = make_estimator()
    t = T0
    est.update(20, t)
    t += BUCKET
    result = est.update(5, t)  # counter fell 15 with nothing aged out yet
    assert est._buckets[max(est._buckets)] == 0.0
    assert result.estimated_occupancy >= 0


def test_dwell_longer_than_window_rejected() -> None:
    try:
        OccupancyEstimator(
            counter_window=timedelta(minutes=60),
            real_dwell=timedelta(minutes=120),
            bucket_size=BUCKET,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
