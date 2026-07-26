"""How busy a club feels: estimated occupancy against its usable floor area.

A headcount on its own says nothing about crowding — 60 people in a 400 m2
studio and 60 in a 1,600 m2 warehouse are very different rooms. Dividing the
estimated real occupancy by the club's floor area gives a figure that is
comparable across clubs, which the reported count is not.

Busyness tracks how packed the floor feels, which is closer to "machines and
walking room occupied" than to raw square metreage. The area_sqm in
clubs.yaml is the gym floor's gross usable area (weights, cardio, studios),
but a chunk of that is walkways, corridors, and the footprint of the racks
and machines themselves — space nobody can stand in regardless of headcount.
A flat reduction before computing density corrects for that dead space so the
result tracks true crowding rather than the room's total floor area.

Thresholds are in people per 16 square metres of that effective area — a more
human-scaled unit than people per m2, since 16 m2 is roughly a small studio's
worth of gym-floor space. 0.8 people per 16m2 is one person per 20 m2, 1.6 is
one per 10 m2.

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

_DENSITY_UNIT_SQM = 16.0

# Fraction of the configured area_sqm that's walkways, corridors, and
# rack/machine footprint rather than space people can occupy.
_DEAD_SPACE_FRACTION = 0.33


class Busyness(StrEnum):
    """The crowding band a club's density falls into."""

    QUIET = "quiet"
    BUSY = "busy"
    CROWDED = "crowded"


@dataclass(frozen=True)
class DensityReading:
    """One club's crowding snapshot for a single poll."""

    people_per_16sqm: float
    sqm_per_person: float | None
    band: Busyness
    people: int
    area_sqm: float
    effective_area_sqm: float


def measure_density(
    people: int,
    area_sqm: float,
    busy_threshold: float,
    crowded_threshold: float,
) -> DensityReading:
    """Classify `people` spread over `area_sqm` into a crowding band.

    `area_sqm` is the gross configured floor area; density is measured
    against that area with dead space subtracted (see module docstring).

    Raises ValueError for a non-positive area, which would otherwise divide by
    zero; callers treat a club with no usable area as simply having no density.
    """
    if area_sqm <= 0:
        raise ValueError(f"area_sqm must be positive, got {area_sqm}")
    people = max(0, people)

    effective_area_sqm = area_sqm * (1 - _DEAD_SPACE_FRACTION)
    density = people / effective_area_sqm * _DENSITY_UNIT_SQM
    if density >= crowded_threshold:
        band = Busyness.CROWDED
    elif density >= busy_threshold:
        band = Busyness.BUSY
    else:
        band = Busyness.QUIET

    return DensityReading(
        people_per_16sqm=round(density, 4),
        # An empty club has no meaningful space-per-person figure.
        sqm_per_person=round(effective_area_sqm / people, 1) if people else None,
        band=band,
        people=people,
        area_sqm=area_sqm,
        effective_area_sqm=round(effective_area_sqm, 1),
    )
