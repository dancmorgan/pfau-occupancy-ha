"""Parsing for clubs.yaml — the maintainer-authored per-club facts.

Floor area and staffed hours are not in the portal's occupancy endpoint (or
anywhere else in its API that we've found), so they live in a YAML file with
this schema instead. If Planet Fitness ever exposes them, this module is the
seam to replace.

These are objective facts about a club (its floor area, its hours), authored
once in the repo's clubs.yaml and fetched by every install — never edited or
overridden per-user. If a figure is wrong, that's a GitHub issue against the
repo, not a local file to hand-edit. Subjective settings (what counts as
"busy", the occupancy reduction percentage) are a different thing entirely:
those are per-user preferences, configured through the integration's Options
in the Home Assistant UI — see config_flow.py and coordinator.thresholds.

Clubs are keyed by the same slug the entities use (`Club.key`), so a club
present in the API but absent from the data simply has no profile, and its
area/staffing/busyness sensors report unavailable.

This module only parses text — it doesn't know where that text came from (a
bundled file or a GitHub fetch); coordinator.py owns fetching, caching and
reading those sources. Deliberately free of Home Assistant imports so it can
be unit-tested with plain pytest. Uses PyYAML, which ships with Home
Assistant core.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .hours import ClubSchedule, ScheduleError, parse_schedule

_LOGGER = logging.getLogger(__name__)

CLUB_DATA_FILENAME = "clubs.yaml"

_ALLOWED_KEYS = frozenset({"name", "area_sqm", "timezone", "open", "staffed"})


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


def load_club_profiles(path: Path) -> dict[str, ClubProfile]:
    """Parse a clubs.yaml-formatted file into profiles keyed by club slug.

    A missing file is not an error — it just means no club has a profile yet.
    Malformed content raises ClubDataError; the caller decides whether that
    should be fatal.
    """
    if not path.is_file():
        _LOGGER.debug("No club data file at %s", path)
        return {}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ClubDataError(f"Could not read {path.name}: {err}") from err

    return parse_club_data(text, path.name)


def parse_club_data(text: str, source: str) -> dict[str, ClubProfile]:
    """Parse clubs.yaml-formatted text into profiles keyed by club slug.

    `source` names where `text` came from (a filename, URL, ...) — used only
    to make ClubDataError messages point somewhere useful.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ClubDataError(f"Could not parse {source}: {err}") from err

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ClubDataError(f"{source}: expected a mapping at the top level")

    clubs = raw.get("clubs")
    if clubs is None:
        return {}
    if not isinstance(clubs, dict):
        raise ClubDataError(f"{source}: 'clubs' must be a mapping of slug to club")

    return {
        str(key): _parse_club(str(key), value, source)
        for key, value in clubs.items()
    }


def _parse_club(key: str, raw: object, source: str) -> ClubProfile:
    where = f"{source}: clubs.{key}"
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
    )


def _positive_number(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClubDataError(f"{where}: expected a number, got {value!r}")
    if value <= 0:
        raise ClubDataError(f"{where}: must be greater than zero, got {value}")
    return float(value)
