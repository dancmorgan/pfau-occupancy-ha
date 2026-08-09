"""Unit tests for the crowding model."""
from __future__ import annotations

import pytest

from conftest import load_module

_density = load_module("density")
Busyness = _density.Busyness
measure_density = _density.measure_density
people_at_threshold = _density.people_at_threshold
effective_area = _density.effective_area

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


class TestPeopleAtThreshold:
    """The inverse of the banding: what headcount does a threshold mean here?"""

    def test_lands_on_the_exact_headcount(self) -> None:
        # area=2000 -> 1340 m2 effective; 1.8 people/36m2 is exactly 67.
        assert people_at_threshold(2000, BUSY) == 67
        assert people_at_threshold(2000, CROWDED) == 134

    def test_rounds_up_to_the_first_qualifying_headcount(self) -> None:
        # 1.8 * 670 / 36 = 33.5 — 33 people aren't busy yet, 34 are.
        assert people_at_threshold(1000, BUSY) == 34

    @pytest.mark.parametrize("area", [400, 650, 1000, 1234, 1800, 2107, 5000])
    @pytest.mark.parametrize("threshold", [0.5, 1.8, 3.6, 4, 6, 7.25])
    def test_agrees_with_measure_density(self, area: float, threshold: float) -> None:
        """The returned headcount must be the first one to reach the band.

        This is the property that matters: whatever the sensor reports as
        "busy at N people" has to be the same N that flips the Busyness
        sensor, for every club size.
        """
        at = people_at_threshold(area, threshold)
        assert measure_density(at, area, threshold, threshold).band is Busyness.CROWDED
        if at > 0:
            assert (
                measure_density(at - 1, area, threshold, threshold).band
                is Busyness.QUIET
            )

    def test_scales_with_club_size(self) -> None:
        """The point of the sensor: one threshold, very different headcounts."""
        assert people_at_threshold(400, BUSY) < people_at_threshold(2000, BUSY)

    def test_non_positive_area_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            people_at_threshold(0, BUSY)


def test_effective_area_matches_the_reading() -> None:
    """The helper is the same area the reading reports, before rounding.

    effective_area stays unrounded so the density maths keeps its precision;
    DensityReading rounds only for display.
    """
    assert effective_area(1000) == pytest.approx(670.0)
    assert measure(50, area=1000).effective_area_sqm == round(effective_area(1000), 1)
