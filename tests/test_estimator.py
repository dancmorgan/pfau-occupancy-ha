"""Unit tests for the static occupancy-reduction estimator.

The estimator module has no Home Assistant imports, so these run under plain
pytest: `pytest tests/` from the repo root.
"""
from __future__ import annotations

from conftest import load_module

estimate_occupancy = load_module("estimator").estimate_occupancy


def test_default_reduction() -> None:
    result = estimate_occupancy(100, 33)
    assert result.raw_count == 100
    assert result.estimated_occupancy == 67


def test_zero_reduction_is_passthrough() -> None:
    result = estimate_occupancy(50, 0)
    assert result.estimated_occupancy == 50


def test_rounds_to_nearest_integer() -> None:
    result = estimate_occupancy(10, 33)  # 10 * 0.67 = 6.7
    assert result.estimated_occupancy == 7


def test_full_reduction_clamped_to_zero() -> None:
    result = estimate_occupancy(10, 100)
    assert result.estimated_occupancy == 0


def test_zero_raw_count() -> None:
    result = estimate_occupancy(0, 33)
    assert result.estimated_occupancy == 0
    assert result.raw_count == 0
