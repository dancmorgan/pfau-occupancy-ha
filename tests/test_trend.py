"""Unit tests for the occupancy trend model."""
from __future__ import annotations

import pytest

from conftest import load_module

_trend = load_module("trend")
Trend = _trend.Trend
TrendSample = _trend.TrendSample
DEFAULT_TREND = _trend.DEFAULT_TREND
measure_trend = _trend.measure_trend
within_window = _trend.within_window

# The integration's deadband: slower than 2 people/hour isn't real movement.
DEADBAND = 2.0

FIVE_MIN = 300.0


def series(*people: int, step: float = FIVE_MIN) -> list[TrendSample]:
    """Samples `step` seconds apart, oldest first."""
    return [TrendSample(at=i * step, people=p) for i, p in enumerate(people)]


class TestDirection:
    def test_steadily_filling_is_rising(self) -> None:
        assert measure_trend(series(20, 25, 30, 35), DEADBAND).direction is Trend.RISING

    def test_steadily_emptying_is_falling(self) -> None:
        assert measure_trend(series(35, 30, 25, 20), DEADBAND).direction is Trend.FALLING

    def test_gradient_is_people_per_hour(self) -> None:
        # +5 people every 5 minutes is +60/hour.
        assert measure_trend(series(20, 25, 30, 35), DEADBAND).gradient == 60.0

    def test_a_single_dip_does_not_flip_a_rising_club(self) -> None:
        """The whole reason for fitting a line instead of diffing two polls."""
        reading = measure_trend(series(20, 26, 32, 30, 44), DEADBAND)
        assert reading.direction is Trend.RISING

    def test_flat_club_holds_its_previous_direction(self) -> None:
        reading = measure_trend(series(30, 30, 30, 30), DEADBAND, previous=Trend.RISING)
        assert reading.direction is Trend.RISING
        assert reading.gradient == 0.0

    def test_drift_inside_the_deadband_holds(self) -> None:
        # +1 person over an hour is well under 2/hour.
        samples = [TrendSample(at=0, people=30), TrendSample(at=3600, people=31)]
        assert measure_trend(samples, DEADBAND, previous=Trend.FALLING).direction is (
            Trend.FALLING
        )

    def test_deadband_is_inclusive_at_its_edge(self) -> None:
        samples = [TrendSample(at=0, people=30), TrendSample(at=3600, people=32)]
        assert measure_trend(samples, DEADBAND).direction is Trend.RISING

    def test_holding_can_keep_a_direction_opposing_the_gradient(self) -> None:
        """A club coasting down from a climb still reads 'busier' until it means it.

        Documents the hysteresis deliberately: the sign of the gradient and
        the reported direction can disagree inside the deadband.
        """
        samples = [TrendSample(at=0, people=30), TrendSample(at=3600, people=29)]
        reading = measure_trend(samples, DEADBAND, previous=Trend.RISING)
        assert reading.gradient < 0
        assert reading.direction is Trend.RISING


class TestUnestablished:
    """Before a line can be fitted the reading falls back to DEFAULT_TREND.

    The sensor is never unknown, so downstream automations never have to
    handle an absent state; `established` is what marks the difference
    between an assumed direction and a measured one.
    """

    def test_the_default_is_rising(self) -> None:
        assert DEFAULT_TREND is Trend.RISING

    def test_no_samples(self) -> None:
        reading = measure_trend([], DEADBAND)
        assert reading.direction is DEFAULT_TREND
        assert reading.established is False
        assert reading.samples == 0

    def test_one_sample_cannot_fit_a_line(self) -> None:
        reading = measure_trend(series(30), DEADBAND)
        assert reading.direction is DEFAULT_TREND
        assert reading.established is False

    def test_identical_timestamps_cannot_fit_a_line(self) -> None:
        samples = [TrendSample(at=5.0, people=10), TrendSample(at=5.0, people=90)]
        reading = measure_trend(samples, DEADBAND)
        assert reading.direction is DEFAULT_TREND
        assert reading.established is False

    def test_a_fitted_line_is_established_even_inside_the_deadband(self) -> None:
        samples = [TrendSample(at=0, people=30), TrendSample(at=3600, people=30)]
        reading = measure_trend(samples, DEADBAND)
        assert reading.established is True
        assert reading.direction is DEFAULT_TREND

    def test_an_explicit_previous_survives_a_gap_in_samples(self) -> None:
        """A club that stops reporting keeps its direction, not the default."""
        reading = measure_trend([], DEADBAND, previous=Trend.FALLING)
        assert reading.direction is Trend.FALLING
        assert reading.established is False

    def test_measured_readings_are_established(self) -> None:
        assert measure_trend(series(20, 25, 30, 35), DEADBAND).established is True


class TestReadingMetadata:
    def test_reports_span_change_and_count(self) -> None:
        reading = measure_trend(series(20, 25, 30, 35), DEADBAND)
        assert reading.samples == 4
        assert reading.span_seconds == 3 * FIVE_MIN
        assert reading.change == 15

    def test_unordered_input_is_sorted_first(self) -> None:
        shuffled = list(reversed(series(20, 25, 30, 35)))
        reading = measure_trend(shuffled, DEADBAND)
        assert reading.direction is Trend.RISING
        assert reading.change == 15


class TestWindow:
    def test_drops_samples_older_than_the_window(self) -> None:
        samples = series(10, 20, 30, 40)  # 0, 300, 600, 900 seconds
        kept = within_window(samples, now=900, window_seconds=610)
        assert [s.people for s in kept] == [20, 30, 40]

    def test_keeps_a_minimum_so_a_slow_poller_still_trends(self) -> None:
        """A scan interval wider than the window must not starve the fit."""
        samples = series(10, 20, step=7200)  # two hours apart
        kept = within_window(samples, now=7200, window_seconds=600)
        assert len(kept) == 2

    def test_keeps_everything_inside_the_window(self) -> None:
        samples = series(10, 20, 30)
        assert within_window(samples, now=600, window_seconds=3600) == samples

    def test_empty_stays_empty(self) -> None:
        assert within_window([], now=0, window_seconds=600) == []


def test_a_realistic_evening_emptying_out() -> None:
    """Morayfield-ish numbers winding down over 40 minutes of 5-minute polls.

    Nine samples spanning 40 minutes for a net -26 people, so the endpoints
    alone imply -39/hour; the fit lands just inside that because of the one
    uptick partway through.
    """
    reading = measure_trend(series(96, 92, 89, 91, 84, 80, 77, 74, 70), DEADBAND)
    assert reading.direction is Trend.FALLING
    assert reading.change == -26
    assert reading.span_seconds == 8 * FIVE_MIN
    assert reading.gradient == pytest.approx(-38.6, abs=0.1)
