"""Unit tests for the weekly opening-hours model."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import load_module

_hours = load_module("hours")
ScheduleError = _hours.ScheduleError
Staffing = _hours.Staffing
parse_schedule = _hours.parse_schedule

BNE = ZoneInfo("Australia/Brisbane")
# Sydney observes DST; Brisbane does not. Used for the DST test below.
SYD = ZoneInfo("Australia/Sydney")


def at(year: int, month: int, day: int, hour: int, minute: int = 0, tz=BNE):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


# 2026-07-27 is a Monday; the week below runs Mon 27th to Sun 2 Aug.
MONDAY = (2026, 7, 27)
SATURDAY = (2026, 8, 1)
SUNDAY = (2026, 8, 2)


def staffed_weekday_club():
    """Open 24/7, staffed 09:00-13:00 and 15:00-19:00 on weekdays only."""
    return parse_schedule(
        None, {"weekdays": ["09:00-13:00", "15:00-19:00"], "sat": "09:00-13:00"}
    )


class TestStateAt:
    def test_inside_a_staffed_window(self) -> None:
        assert staffed_weekday_club().state_at(at(*MONDAY, 10)) is Staffing.STAFFED

    def test_between_two_staffed_windows_is_unstaffed(self) -> None:
        assert staffed_weekday_club().state_at(at(*MONDAY, 14)) is Staffing.UNSTAFFED

    def test_overnight_on_a_247_club_is_unstaffed_not_closed(self) -> None:
        assert staffed_weekday_club().state_at(at(*MONDAY, 3)) is Staffing.UNSTAFFED

    def test_day_with_no_staffed_entry(self) -> None:
        assert staffed_weekday_club().state_at(at(*SUNDAY, 10)) is Staffing.UNSTAFFED

    def test_window_start_is_inclusive_and_end_exclusive(self) -> None:
        schedule = staffed_weekday_club()
        assert schedule.state_at(at(*MONDAY, 9, 0)) is Staffing.STAFFED
        assert schedule.state_at(at(*MONDAY, 13, 0)) is Staffing.UNSTAFFED

    def test_closed_outside_opening_hours(self) -> None:
        schedule = parse_schedule({"daily": "05:00-22:00"}, {"daily": "09:00-17:00"})
        assert schedule.state_at(at(*MONDAY, 4)) is Staffing.CLOSED
        assert schedule.state_at(at(*MONDAY, 6)) is Staffing.UNSTAFFED
        assert schedule.state_at(at(*MONDAY, 10)) is Staffing.STAFFED
        assert schedule.state_at(at(*MONDAY, 23)) is Staffing.CLOSED

    def test_closed_wins_over_staffed(self) -> None:
        """A staffed window outside opening hours is a data error, not a state."""
        schedule = parse_schedule({"daily": "09:00-17:00"}, {"daily": "08:00-18:00"})
        assert schedule.state_at(at(*MONDAY, 8, 30)) is Staffing.CLOSED

    def test_end_of_day_does_not_bleed_into_tomorrow(self) -> None:
        schedule = parse_schedule({"mon": "05:00-24:00"}, None)
        assert schedule.state_at(at(*MONDAY, 23, 59)) is Staffing.UNSTAFFED
        # Tuesday has no opening hours at all.
        assert schedule.state_at(at(2026, 7, 28, 0, 30)) is Staffing.CLOSED

    def test_window_wrapping_past_midnight(self) -> None:
        schedule = parse_schedule({"mon": "22:00-02:00"}, None)
        assert schedule.state_at(at(*MONDAY, 23)) is Staffing.UNSTAFFED
        assert schedule.state_at(at(2026, 7, 28, 1)) is Staffing.UNSTAFFED
        assert schedule.state_at(at(2026, 7, 28, 3)) is Staffing.CLOSED

    def test_no_open_table_means_always_open(self) -> None:
        assert parse_schedule(None, None).state_at(at(*MONDAY, 3)) is Staffing.UNSTAFFED

    def test_empty_open_table_means_never_open(self) -> None:
        assert parse_schedule({}, None).state_at(at(*MONDAY, 10)) is Staffing.CLOSED


class TestNextChange:
    def test_finds_the_next_boundary(self) -> None:
        change = staffed_weekday_club().next_change(at(*MONDAY, 10))
        assert change == at(*MONDAY, 13)

    def test_skips_boundaries_that_do_not_change_state(self) -> None:
        """Back-to-back staffed windows shouldn't report a change at the seam."""
        schedule = parse_schedule(None, {"mon": ["09:00-13:00", "13:00-17:00"]})
        assert schedule.next_change(at(*MONDAY, 10)) == at(*MONDAY, 17)

    def test_rolls_over_the_weekend_to_the_next_staffed_day(self) -> None:
        # Saturday is staffed 09:00-13:00; from Sat 14:00 the next change is
        # Monday morning, since Sunday has no staffing.
        change = staffed_weekday_club().next_change(at(*SATURDAY, 14))
        assert change == at(2026, 8, 3, 9)

    def test_constant_schedule_has_no_next_change(self) -> None:
        assert parse_schedule(None, None).next_change(at(*MONDAY, 10)) is None

    def test_change_lands_on_the_new_state(self) -> None:
        schedule = staffed_weekday_club()
        now = at(*MONDAY, 14)
        change = schedule.next_change(now)
        assert schedule.state_at(change) is not schedule.state_at(now)

    def test_dst_shift_keeps_wall_clock_hours(self) -> None:
        """Sydney springs forward 02:00->03:00 on 2026-10-04 (a Sunday).

        Staffing is defined in wall-clock time, so Sunday's 09:00 opening is
        still 09:00 local on the day of the shift.
        """
        schedule = parse_schedule(None, {"sun": "09:00-13:00"})
        assert schedule.state_at(at(2026, 10, 4, 8, 59, tz=SYD)) is Staffing.UNSTAFFED
        assert schedule.state_at(at(2026, 10, 4, 9, 1, tz=SYD)) is Staffing.STAFFED
        assert schedule.next_change(
            at(2026, 10, 4, 1, tz=SYD)
        ) == at(2026, 10, 4, 9, tz=SYD)


class TestParsing:
    def test_day_aliases_expand(self) -> None:
        schedule = parse_schedule(None, {"weekends": "10:00-14:00"})
        assert {span.weekday for span in schedule.staffed_spans} == {5, 6}

    def test_single_range_need_not_be_a_list(self) -> None:
        assert len(parse_schedule(None, {"mon": "09:00-17:00"}).staffed_spans) == 1

    def test_spans_round_trip_to_text(self) -> None:
        schedule = parse_schedule(
            None, {"mon": ["09:00-13:00", "22:00-02:00", "05:00-24:00"]}
        )
        assert schedule.staffed_text_for(
            datetime(*MONDAY).date()
        ) == ["09:00-13:00", "22:00-02:00", "05:00-24:00"]

    @pytest.mark.parametrize(
        "table",
        [
            {"funday": "09:00-17:00"},
            {"mon": "9am-5pm"},
            {"mon": "09:00"},
            {"mon": "09:00-25:00"},
            {"mon": "aa:bb-09:00"},
            {"mon": 900},
            {"mon": {"start": "09:00"}},
        ],
    )
    def test_malformed_tables_are_rejected(self, table) -> None:
        with pytest.raises(ScheduleError):
            parse_schedule(None, table)

    def test_non_mapping_table_is_rejected(self) -> None:
        with pytest.raises(ScheduleError):
            parse_schedule(None, ["09:00-17:00"])
