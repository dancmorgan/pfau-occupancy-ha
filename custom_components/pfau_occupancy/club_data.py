"""Loader for clubs.yaml — the hand-maintained per-club facts.

Floor area and staffed hours are not in the portal's occupancy endpoint (or
anywhere else in its API that we've found), so they live in a YAML file in
this directory that is edited by hand and shipped with the integration. If
Planet Fitness ever exposes them, this module is the seam to replace.

Clubs are keyed by the same slug the entities use (`Club.key`), so a club
present in the API but absent from the file simply has no profile, and its
area/staffing/busyness sensors report unavailable.

Deliberately free of Home Assistant imports so it can be unit-tested with
plain pytest. Uses PyYAML, which ships with Home Assistant core.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .hours import ClubSchedule, ScheduleError, parse_schedule

_LOGGER = logging.getLogger(__name__)

CLUB_DATA_FILENAME = "clubs.yaml"

_ALLOWED_KEYS = frozenset(
    {"name", "area_sqm", "timezone", "open", "staffed", "busy", "crowded"}
)


class ClubDataError(ValueError):
    """clubs.yaml is malformed."""


@dataclass(frozen=True)
class ClubProfile:
    """The static facts we hold about one club."""

    key: str
    # Informational only — the API's club name is what entities are named
    # from. Kept so the YAML is readable when the slug isn't obvious.
    name: str | None
    area_sqm: float | None
    timezone: str | None
    schedule: ClubSchedule | None
    # Per-club threshold overrides; None means "use the integration options".
    busy_threshold: float | None
    crowded_threshold: float | None


def load_club_profiles(path: Path) -> dict[str, ClubProfile]:
    """Parse clubs.yaml into profiles keyed by club slug.

    A missing file is not an error — it just means no club has a profile yet.
    Malformed content raises ClubDataError; the caller decides whether that
    should be fatal.
    """
    if not path.is_file():
        _LOGGER.debug("No club data file at %s", path)
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        raise ClubDataError(f"Could not read {path.name}: {err}") from err

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ClubDataError(f"{path.name}: expected a mapping at the top level")

    clubs = raw.get("clubs")
    if clubs is None:
        return {}
    if not isinstance(clubs, dict):
        raise ClubDataError(f"{path.name}: 'clubs' must be a mapping of slug to club")

    return {
        str(key): _parse_club(str(key), value, path.name)
        for key, value in clubs.items()
    }


def _parse_club(key: str, raw: object, filename: str) -> ClubProfile:
    where = f"{filename}: clubs.{key}"
    if not isinstance(raw, dict):
        raise ClubDataError(f"{where}: expected a mapping")

    # Catch typos loudly rather than silently ignoring a misspelled field —
    # a silently dropped "staffed" key would look like a working config.
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise ClubDataError(
            f"{where}: unknown field(s) {', '.join(sorted(unknown))} "
            f"(expected {', '.join(sorted(_ALLOWED_KEYS))})"
        )

    busy = _positive_number(raw.get("busy"), f"{where}.busy")
    crowded = _positive_number(raw.get("crowded"), f"{where}.crowded")
    if busy is not None and crowded is not None and crowded < busy:
        raise ClubDataError(f"{where}: crowded ({crowded}) is below busy ({busy})")

    # No hours at all means no staffing sensor for this club, which is a
    # different thing from "open 24/7 and never staffed".
    schedule: ClubSchedule | None = None
    if "open" in raw or "staffed" in raw:
        try:
            schedule = parse_schedule(raw.get("open"), raw.get("staffed"))
        except ScheduleError as err:
            raise ClubDataError(f"{where}: {err}") from err

    return ClubProfile(
        key=key,
        name=raw.get("name"),
        area_sqm=_positive_number(raw.get("area_sqm"), f"{where}.area_sqm"),
        timezone=raw.get("timezone"),
        schedule=schedule,
        busy_threshold=busy,
        crowded_threshold=crowded,
    )


def _positive_number(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClubDataError(f"{where}: expected a number, got {value!r}")
    if value <= 0:
        raise ClubDataError(f"{where}: must be greater than zero, got {value}")
    return float(value)
