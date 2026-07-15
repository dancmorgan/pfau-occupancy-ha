"""Unit tests for the static occupancy-reduction estimator.

The estimator module has no Home Assistant imports, so these run under plain
pytest: `pytest tests/` from the repo root.
"""
from __future__ import annotations

import importlib.util
import sys
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
estimate_occupancy = _module.estimate_occupancy


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
