"""Unit tests for the crowding model."""
from __future__ import annotations

import pytest

from conftest import load_module

_density = load_module("density")
Busyness = _density.Busyness
measure_density = _density.measure_density

# One person per 20 m2, then one per 10 m2 — the integration defaults.
BUSY = 1.8
CROWDED = 3.6


def measure(people: int, area: float = 1000):
    return measure_density(people, area, BUSY, CROWDED)


def test_sparse_club_is_quiet() -> None:
    assert measure(20).band is Busyness.QUIET


def test_band_boundaries_are_inclusive() -> None:
    # area=2000 has a 1340 m2 effective area (33% dead space removed), which
    # 67/134 people divide into exactly at the busy/crowded thresholds.
    assert measure(67, area=2000).band is Busyness.BUSY
    assert measure(134, area=2000).band is Busyness.CROWDED
    assert measure(66, area=2000).band is Busyness.QUIET


def test_reciprocal_is_reported() -> None:
    reading = measure(50)
    assert reading.people_per_36sqm == 2.6866
    assert reading.sqm_per_person == 13.4


def test_dead_space_is_subtracted_from_area() -> None:
    """33% of the configured area is walkways/racks, not standing room."""
    reading = measure(50, area=1000)
    assert reading.area_sqm == 1000
    assert reading.effective_area_sqm == 670.0


def test_empty_club_has_no_space_per_person() -> None:
    reading = measure(0)
    assert reading.band is Busyness.QUIET
    assert reading.people_per_36sqm == 0
    assert reading.sqm_per_person is None


def test_same_headcount_different_rooms() -> None:
    """The whole point: 60 people is quiet in a warehouse, crowded in a studio."""
    assert measure(60, area=2400).band is Busyness.QUIET
    assert measure(60, area=400).band is Busyness.CROWDED


def test_negative_headcount_is_floored() -> None:
    assert measure(-5).people == 0


def test_non_positive_area_is_rejected() -> None:
    with pytest.raises(ValueError):
        measure_density(10, 0, BUSY, CROWDED)


def test_equal_thresholds_collapse_to_two_bands() -> None:
    assert measure_density(67, 2000, 1.8, 1.8).band is Busyness.CROWDED
    assert measure_density(66, 2000, 1.8, 1.8).band is Busyness.QUIET
