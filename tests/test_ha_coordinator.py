"""Polling, retries, availability and the clubs.yaml fetch/cache/fallback."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.pfau_occupancy.club_data import ClubDataError
from custom_components.pfau_occupancy.const import (
    CLUB_DATA_CACHE_FILENAME,
    CLUB_DATA_REFRESH_HOURS,
    DEFAULT_AREA_SQM,
)
from ha_support import TEST_CLUBS_YAML, client_error, refresh

COORDINATOR_LOGGER = "custom_components.pfau_occupancy.coordinator"


class TestRetries:
    async def test_a_blip_within_one_poll_is_absorbed(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.clubs[0]["occupancy"] = 60
        portal.fail_polls(502)
        before = portal.fetches

        await refresh(hass, freezer)

        # Failed once, succeeded on the retry; nothing ever went unavailable.
        assert portal.fetches == before + 2
        assert hass.states.get("sensor.morayfield_reported_occupancy").state == "60"

    async def test_two_blips_in_a_row_are_still_absorbed(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.fail_polls(502, client_error())
        before = portal.fetches

        await refresh(hass, freezer)

        assert portal.fetches == before + 3
        assert hass.states.get("sensor.morayfield_reported_occupancy").state == "40"

    async def test_a_sustained_outage_makes_everything_unavailable(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.go_down()

        await refresh(hass, freezer)

        for state in hass.states.async_all():
            assert state.state == "unavailable", state.entity_id

    async def test_recovery_makes_everything_available_again(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.go_down()
        await refresh(hass, freezer)
        portal.recover()
        await refresh(hass, freezer)

        assert hass.states.get("sensor.morayfield_reported_occupancy").state == "40"
        assert hass.states.get("sensor.morayfield_busyness").state == "quiet"

    async def test_outage_is_logged_once_and_recovery_once(
        self, hass, init_integration, portal, freezer, caplog
    ) -> None:
        caplog.set_level(logging.INFO, logger=COORDINATOR_LOGGER)
        portal.go_down()
        await refresh(hass, freezer)
        await refresh(hass, freezer)
        await refresh(hass, freezer)

        assert caplog.text.count("Error fetching") == 1
        # The message comes from the integration's translations.
        assert "Could not reach the Planet Fitness portal" in caplog.text

        portal.recover()
        await refresh(hass, freezer)

        assert caplog.text.count("recovered") == 1

    async def test_credentials_rejected_mid_run_start_reauth(
        self, hass, init_integration, portal, freezer
    ) -> None:
        # The session expired (401), and re-login is refused too.
        portal.outcomes = [401]
        portal.login_status = 401

        await refresh(hass, freezer)

        flows = hass.config_entries.flow.async_progress()
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]
        assert hass.states.get("sensor.morayfield_reported_occupancy").state == (
            "unavailable"
        )


class TestPollData:
    async def test_missing_count_keeps_the_last_estimate(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.clubs[0]["occupancy"] = None

        await refresh(hass, freezer)

        assert hass.states.get("sensor.morayfield_reported_occupancy").state == (
            "unknown"
        )
        # Not a fabricated zero: the previous estimate (20) stands.
        assert hass.states.get("sensor.morayfield_real_occupancy").state == "20"

    async def test_rising_counts_establish_a_rising_trend(
        self, hass, init_integration, portal, freezer
    ) -> None:
        for count in (60, 80, 100, 120):
            portal.clubs[0]["occupancy"] = count
            await refresh(hass, freezer, minutes=5)

        state = hass.states.get("binary_sensor.morayfield_occupancy_trend")
        assert state.state == "on"
        assert state.attributes["established"] is True
        assert state.attributes["gradient_people_per_hour"] > 0
        assert state.attributes["samples"] == 5

    async def test_falling_counts_establish_a_falling_trend(
        self, hass, init_integration, portal, freezer
    ) -> None:
        for count in (30, 20, 10, 4):
            portal.clubs[0]["occupancy"] = count
            await refresh(hass, freezer, minutes=5)

        state = hass.states.get("binary_sensor.morayfield_occupancy_trend")
        assert state.state == "off"
        assert state.attributes["established"] is True
        assert state.attributes["gradient_people_per_hour"] < 0

    async def test_thresholds_as_headcounts(self, hass, init_integration) -> None:
        coordinator = init_integration.runtime_data

        assert coordinator.headcount_thresholds("morayfield") == (30, 41)
        # No clubs.yaml entry: converted against the placeholder area.
        assert coordinator.headcount_thresholds("mystery_club") == (30, 41)

    async def test_a_club_with_no_area_gets_the_placeholder(
        self, hass, init_integration
    ) -> None:
        coordinator = init_integration.runtime_data

        assert coordinator.area_sqm("morayfield") == 1000
        assert not coordinator.area_is_placeholder("morayfield")
        assert coordinator.area_sqm("mystery_club") == DEFAULT_AREA_SQM == 999
        assert coordinator.area_is_placeholder("mystery_club")


class TestClubData:
    async def test_remote_data_is_used_and_cached(
        self, hass, init_integration
    ) -> None:
        coordinator = init_integration.runtime_data

        assert coordinator.profiles["morayfield"].area_sqm == 1000
        assert Path(hass.config.path(CLUB_DATA_CACHE_FILENAME)).is_file()

    async def test_cache_is_used_when_github_is_unreachable(
        self, hass, init_integration, remote
    ) -> None:
        remote.error = client_error()
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()

        coordinator = init_integration.runtime_data
        assert coordinator.profiles["morayfield"].area_sqm == 1000

    async def test_http_error_from_github_falls_back_too(
        self, hass, init_integration, remote
    ) -> None:
        remote.status = 404
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()

        assert init_integration.runtime_data.profiles["morayfield"].area_sqm == 1000

    async def test_bundled_copy_is_used_with_no_remote_and_no_cache(
        self, hass, mock_entry, portal, remote
    ) -> None:
        remote.error = client_error()

        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        # The copy that ships with the integration, not the test data.
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1973

    async def test_malformed_remote_is_ignored(
        self, hass, mock_entry, portal, remote, caplog
    ) -> None:
        remote.text = "clubs: [this is not a mapping"

        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_entry.state is ConfigEntryState.LOADED
        assert "Ignoring malformed remote clubs.yaml" in caplog.text
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1973

    async def test_malformed_cache_is_ignored(
        self, hass, mock_entry, portal, remote, caplog
    ) -> None:
        Path(hass.config.path(CLUB_DATA_CACHE_FILENAME)).write_text(
            "clubs: [this is not a mapping", encoding="utf-8"
        )
        remote.error = client_error()

        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        assert "Ignoring malformed cached clubs.yaml" in caplog.text
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1973

    async def test_unreadable_bundled_data_leaves_no_profiles(
        self, hass, mock_entry, portal, remote, caplog
    ) -> None:
        remote.error = client_error()

        with patch(
            "custom_components.pfau_occupancy.coordinator.load_club_profiles",
            side_effect=ClubDataError("bad file"),
        ):
            await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()

        assert mock_entry.state is ConfigEntryState.LOADED
        assert mock_entry.runtime_data.profiles == {}
        assert "Ignoring bundled club data" in caplog.text
        assert hass.states.get("sensor.morayfield_staffing_status").state == (
            "unavailable"
        )
        # Without any profile every club is measured against the placeholder.
        busyness = hass.states.get("sensor.morayfield_busyness")
        assert busyness.state == "quiet"
        assert busyness.attributes["area_is_placeholder"] is True
        assert hass.states.get("sensor.morayfield_real_occupancy").state == "20"

    async def test_unreadable_cache_does_not_break_setup(
        self, hass, mock_entry, portal, remote
    ) -> None:
        cache = Path(hass.config.path(CLUB_DATA_CACHE_FILENAME))
        cache.write_text(TEST_CLUBS_YAML, encoding="utf-8")
        remote.error = client_error()
        real_read_text = Path.read_text

        def read_text(path: Path, *args, **kwargs):
            if path.name == CLUB_DATA_CACHE_FILENAME:
                raise OSError("unreadable")
            return real_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_text):
            await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()

        # Fell through to the copy that ships with the integration.
        assert mock_entry.state is ConfigEntryState.LOADED
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1973

    async def test_corrupt_cache_bytes_do_not_break_setup(
        self, hass, mock_entry, portal, remote
    ) -> None:
        Path(hass.config.path(CLUB_DATA_CACHE_FILENAME)).write_bytes(bytes([0xFF, 0xFE, 0x00]))
        remote.error = client_error()

        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_entry.state is ConfigEntryState.LOADED
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1973

    async def test_unwritable_cache_does_not_break_setup(
        self, hass, mock_entry, portal, remote
    ) -> None:
        real_write_text = Path.write_text

        def write_text(path: Path, *args, **kwargs):
            if path.name == CLUB_DATA_CACHE_FILENAME:
                raise OSError("read-only")
            return real_write_text(path, *args, **kwargs)

        with patch.object(Path, "write_text", write_text):
            await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()

        assert mock_entry.state is ConfigEntryState.LOADED
        assert mock_entry.runtime_data.profiles["morayfield"].area_sqm == 1000

    async def test_data_is_refetched_periodically(
        self, hass, init_integration, remote, freezer
    ) -> None:
        coordinator = init_integration.runtime_data
        assert coordinator.profiles["morayfield"].area_sqm == 1000

        remote.text = TEST_CLUBS_YAML.replace("area_sqm: 1000", "area_sqm: 2000")
        freezer.tick(timedelta(hours=CLUB_DATA_REFRESH_HOURS, minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

        assert coordinator.profiles["morayfield"].area_sqm == 2000
        busyness = hass.states.get("sensor.morayfield_busyness")
        assert busyness.attributes["area_sqm"] == 2000

    async def test_a_club_gaining_a_profile_gets_its_staffing_sensors(
        self, hass, init_integration, remote, freezer
    ) -> None:
        assert hass.states.get("sensor.mystery_club_staffing_status").state == (
            "unavailable"
        )

        remote.text = TEST_CLUBS_YAML + (
            "  mystery_club:\n"
            "    timezone: Australia/Brisbane\n"
            "    staffed:\n"
            "      weekdays: 09:00-17:00\n"
        )
        freezer.tick(timedelta(hours=CLUB_DATA_REFRESH_HOURS, minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

        # Still the Tuesday 10:00 it was ticked to, inside staffed hours.
        assert hass.states.get("sensor.mystery_club_staffing_status").state == (
            "staffed"
        )

    async def test_unknown_timezone_falls_back_to_home_assistants(
        self, hass, mock_entry, portal, remote, caplog
    ) -> None:
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_entry.runtime_data
        assert "Unknown timezone 'Mars/Olympus' for club badzone" in caplog.text
        assert coordinator.timezone("badzone") is dt_util.DEFAULT_TIME_ZONE
        assert str(coordinator.timezone("morayfield")) == "Australia/Brisbane"

    async def test_missing_profiles_are_named_in_the_debug_log(
        self, hass, mock_entry, portal, remote, caplog
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=COORDINATOR_LOGGER)

        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        assert "mystery_club" in caplog.text
