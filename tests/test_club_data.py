"""Unit tests for the clubs.yaml loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_module

_club_data = load_module("club_data")
ClubDataError = _club_data.ClubDataError
load_club_profiles = _club_data.load_club_profiles
parse_club_data = _club_data.parse_club_data

SHIPPED_FILE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "pfau_occupancy"
    / _club_data.CLUB_DATA_FILENAME
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "clubs.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_file_parses() -> None:
    """The file we ship must always load, however the examples are edited."""
    load_club_profiles(SHIPPED_FILE)


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_club_profiles(tmp_path / "nope.yaml") == {}


def test_empty_file(tmp_path: Path) -> None:
    assert load_club_profiles(write(tmp_path, "")) == {}


def test_empty_clubs_mapping(tmp_path: Path) -> None:
    assert load_club_profiles(write(tmp_path, "clubs: {}")) == {}


def test_full_club(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
clubs:
  morayfield:
    name: Morayfield
    area_sqm: 1200
    timezone: Australia/Brisbane
    staffed:
      weekdays: 09:00-17:00
""",
    )
    profile = load_club_profiles(path)["morayfield"]
    assert profile.name == "Morayfield"
    assert profile.area_sqm == 1200.0
    assert profile.timezone == "Australia/Brisbane"
    assert len(profile.schedule.staffed_spans) == 5
    assert profile.schedule.open_spans is None


def test_club_with_no_hours_has_no_schedule(tmp_path: Path) -> None:
    """Absent hours must not be mistaken for "open 24/7, never staffed"."""
    path = write(tmp_path, "clubs:\n  a_club:\n    area_sqm: 500\n")
    assert load_club_profiles(path)["a_club"].schedule is None


def test_club_with_only_open_hours_gets_a_schedule(tmp_path: Path) -> None:
    path = write(tmp_path, "clubs:\n  a_club:\n    open:\n      daily: 05:00-22:00\n")
    schedule = load_club_profiles(path)["a_club"].schedule
    assert schedule is not None
    assert schedule.staffed_spans == ()


def test_all_fields_optional(tmp_path: Path) -> None:
    path = write(tmp_path, "clubs:\n  a_club: {}\n")
    profile = load_club_profiles(path)["a_club"]
    assert profile.area_sqm is None
    assert profile.schedule is None


@pytest.mark.parametrize(
    "body",
    [
        "clubs:\n  a_club:\n    aera_sqm: 500\n",  # typo in a field name
        "clubs:\n  a_club:\n    area_sqm: 0\n",
        "clubs:\n  a_club:\n    area_sqm: -5\n",
        "clubs:\n  a_club:\n    area_sqm: big\n",
        "clubs:\n  a_club:\n    busy: 0.10\n",  # busy/crowded no longer a clubs.yaml field
        "clubs:\n  a_club:\n    staffed:\n      mon: nine to five\n",
        "clubs:\n  a_club: 500\n",
        "clubs: [a_club]\n",
        "- just\n- a\n- list\n",
        "clubs:\n  a_club:\n    area_sqm: 500\n   bad_indent: 1\n",  # invalid YAML
    ],
)
def test_malformed_content_is_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ClubDataError):
        load_club_profiles(write(tmp_path, body))


def test_error_message_names_the_club(tmp_path: Path) -> None:
    with pytest.raises(ClubDataError, match="clubs.morayfield"):
        load_club_profiles(
            write(tmp_path, "clubs:\n  morayfield:\n    area_sqm: -1\n")
        )


def test_parse_club_data_matches_load_club_profiles(tmp_path: Path) -> None:
    """load_club_profiles is just parse_club_data fed from a file."""
    text = "clubs:\n  a_club:\n    area_sqm: 500\n"
    from_text = parse_club_data(text, "some/source")
    from_file = load_club_profiles(write(tmp_path, text))
    assert from_text == from_file


def test_parse_club_data_error_names_its_source() -> None:
    with pytest.raises(ClubDataError, match="my-source"):
        parse_club_data("clubs:\n  a_club:\n    area_sqm: -1\n", "my-source")
