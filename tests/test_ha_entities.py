"""Entity states, attributes, registry settings and schedule timers."""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from ha_support import refresh

DISABLED_BY_DEFAULT = (
    "sensor.morayfield_floor_area",
    "sensor.morayfield_busy_threshold",
    "sensor.morayfield_crowded_threshold",
)


async def _enable_all(hass, entry) -> None:
    """Enable the disabled-by-default entities and reload to create them."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.disabled_by is not None:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


class TestOccupancySensors:
    async def test_reported_occupancy(self, hass, init_integration) -> None:
        state = hass.states.get("sensor.morayfield_reported_occupancy")

        assert state.state == "40"
        assert state.attributes["unit_of_measurement"] == "people"
        assert state.attributes["address"] == "1 Morayfield Street"
        # The new portal reports no capacity, so there is no limit to compare to.
        assert state.attributes["limit"] is None
        assert state.attributes["percent_full"] is None

    async def test_real_occupancy_applies_the_reduction(
        self, hass, init_integration
    ) -> None:
        state = hass.states.get("sensor.morayfield_real_occupancy")

        assert state.state == "20"
        assert state.attributes["raw_count"] == 40
        assert state.attributes["reduction_percent"] == 50


class TestBusyness:
    @pytest.mark.parametrize(
        ("reported", "band"), [(40, "quiet"), (80, "busy"), (100, "crowded")]
    )
    async def test_bands(
        self, hass, init_integration, portal, freezer, reported: int, band: str
    ) -> None:
        portal.clubs[0]["occupancy"] = reported
        await refresh(hass, freezer)

        state = hass.states.get("sensor.morayfield_busyness")
        assert state.state == band
        assert state.attributes["area_sqm"] == 1000
        assert state.attributes["effective_area_sqm"] == 670
        assert state.attributes["busy_at_people"] == 30
        assert state.attributes["crowded_at_people"] == 41

    async def test_a_club_with_no_floor_area_uses_the_placeholder(
        self, hass, init_integration
    ) -> None:
        state = hass.states.get("sensor.mystery_club_busyness")

        assert state.state == "quiet"
        assert state.attributes["area_sqm"] == 999
        assert state.attributes["area_is_placeholder"] is True
        assert state.attributes["busy_at_people"] == 30
        assert state.attributes["crowded_at_people"] == 41

    async def test_a_clubs_own_floor_area_is_not_flagged(
        self, hass, init_integration
    ) -> None:
        state = hass.states.get("sensor.eastside_busyness")

        assert state.attributes["area_is_placeholder"] is False
        assert state.attributes["busy_at_people"] == 15


class TestDisabledByDefault:
    async def test_static_diagnostics_start_disabled(
        self, hass, init_integration
    ) -> None:
        registry = er.async_get(hass)

        for entity_id in DISABLED_BY_DEFAULT:
            entry = registry.async_get(entity_id)
            assert entry.disabled_by is RegistryEntryDisabler.INTEGRATION, entity_id
            assert entry.entity_category is EntityCategory.DIAGNOSTIC, entity_id
            assert hass.states.get(entity_id) is None, entity_id

    async def test_everything_else_starts_enabled(
        self, hass, init_integration
    ) -> None:
        registry = er.async_get(hass)

        for entity in er.async_entries_for_config_entry(
            registry, init_integration.entry_id
        ):
            if entity.entity_id not in DISABLED_BY_DEFAULT and "morayfield" in (
                entity.entity_id
            ):
                assert entity.disabled_by is None, entity.entity_id
                assert entity.entity_category is None, entity.entity_id

    async def test_disabling_them_does_not_change_busyness(
        self, hass, init_integration
    ) -> None:
        # Density is computed from the coordinator's profiles, not the sensors.
        assert hass.states.get("sensor.morayfield_busyness").state == "quiet"
        assert hass.states.get("sensor.morayfield_busyness").attributes[
            "people_per_36sqm"
        ] == pytest.approx(1.0746, abs=1e-4)

    async def test_floor_area_once_enabled(self, hass, init_integration) -> None:
        await _enable_all(hass, init_integration)

        state = hass.states.get("sensor.morayfield_floor_area")
        assert float(state.state) == 1000
        assert state.attributes["device_class"] == "area"
        assert state.attributes["unit_of_measurement"] == "m²"
        # The placeholder only feeds Busyness; it is never shown as the area.
        assert hass.states.get("sensor.mystery_club_floor_area").state == (
            "unavailable"
        )

    async def test_band_thresholds_once_enabled(self, hass, init_integration) -> None:
        await _enable_all(hass, init_integration)

        busy = hass.states.get("sensor.morayfield_busy_threshold")
        crowded = hass.states.get("sensor.morayfield_crowded_threshold")
        assert busy.state == "30"
        assert crowded.state == "41"
        assert busy.attributes["unit_of_measurement"] == "people"
        assert busy.attributes["band"] == "busy"
        assert busy.attributes["people_per_36sqm_threshold"] == 1.6
        assert crowded.attributes["people_per_36sqm_threshold"] == 2.2
        assert busy.attributes["area_sqm"] == 1000
        assert busy.attributes["effective_area_sqm"] == 670
        assert busy.attributes["crowded_at_people"] == 41
        assert busy.attributes["area_is_placeholder"] is False

        placeholder = hass.states.get("sensor.mystery_club_busy_threshold")
        assert placeholder.state == "30"
        assert placeholder.attributes["area_sqm"] == 999
        assert placeholder.attributes["area_is_placeholder"] is True


class TestStaffing:
    async def test_staffed_during_the_day(self, hass, init_integration) -> None:
        state = hass.states.get("sensor.morayfield_staffing_status")

        assert state.state == "staffed"
        assert state.attributes["staffed_today"] == ["09:00-13:00", "15:00-19:00"]
        assert state.attributes["staffed_tomorrow"] == ["09:00-13:00", "15:00-19:00"]
        assert state.attributes["timezone"] == "Australia/Brisbane"

    async def test_next_change_is_a_timestamp(self, hass, init_integration) -> None:
        state = hass.states.get("sensor.morayfield_next_staff_status_change")

        # 13:00 in Brisbane.
        assert state.state == "2026-10-19T03:00:00+00:00"
        assert state.attributes["device_class"] == "timestamp"

    async def test_sensors_flip_on_schedule_without_a_poll(
        self, hass, init_integration, freezer
    ) -> None:
        freezer.move_to("2026-10-19 03:00:30+00:00")  # 13:00:30 in Brisbane
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

        assert hass.states.get("sensor.morayfield_staffing_status").state == (
            "unstaffed"
        )
        # Re-armed for the afternoon session, 15:00 in Brisbane.
        assert hass.states.get("sensor.morayfield_next_staff_status_change").state == (
            "2026-10-19T05:00:00+00:00"
        )

    async def test_closed_outside_opening_hours(
        self, hass, init_integration, freezer
    ) -> None:
        assert hass.states.get("sensor.eastside_staffing_status").state == "staffed"

        freezer.move_to("2026-10-19 12:00:00+00:00")  # 23:00 in Sydney
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

        assert hass.states.get("sensor.eastside_staffing_status").state == "closed"

    async def test_unavailable_without_hours(self, hass, init_integration) -> None:
        for club in ("badzone", "mystery_club"):
            assert hass.states.get(f"sensor.{club}_staffing_status").state == (
                "unavailable"
            )
            assert hass.states.get(
                f"sensor.{club}_next_staff_status_change"
            ).state == "unavailable"

    async def test_timers_are_cancelled_on_unload(
        self, hass, init_integration
    ) -> None:
        await hass.config_entries.async_unload(init_integration.entry_id)
        await hass.async_block_till_done()

        # Nothing left to fire (the harness also fails on lingering timers).
        assert hass.states.get("sensor.morayfield_staffing_status").state == (
            "unavailable"
        )


class TestOccupancyTrendSensor:
    async def test_starts_on_the_assumed_default(self, hass, init_integration) -> None:
        state = hass.states.get("binary_sensor.morayfield_occupancy_trend")

        assert state.state == "on"
        assert state.attributes["established"] is False
        assert state.attributes["samples"] == 1
        assert state.attributes["deadband_people_per_hour"] == 10.0
        assert state.attributes["configured_window_minutes"] == 60

    async def test_has_no_device_class(self, hass, init_integration) -> None:
        state = hass.states.get("binary_sensor.morayfield_occupancy_trend")

        assert "device_class" not in state.attributes

    async def test_unavailable_attributes_when_the_club_is_gone(
        self, hass, init_integration, portal, freezer
    ) -> None:
        portal.clubs = [c for c in portal.clubs if c["name"] != "Morayfield"]
        # The client caches the studio list for 6 hours, so a club opening or
        # closing is only noticed once that has passed.
        await refresh(hass, freezer, minutes=7 * 60)

        state = hass.states.get("binary_sensor.morayfield_occupancy_trend")
        assert state.state == "unavailable"
        assert state.attributes.get("established") is None
