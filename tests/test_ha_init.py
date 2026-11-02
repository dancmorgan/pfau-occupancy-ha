"""Setup, unload, devices and the update lifecycle."""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.pfau_occupancy import async_remove_config_entry_device
from custom_components.pfau_occupancy.const import DOMAIN, MEMBER_PORTAL_URL
from ha_support import club_json, refresh


async def test_setup_and_unload(hass, init_integration) -> None:
    assert init_integration.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_each_club_is_a_device(hass, init_integration) -> None:
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, "morayfield"), init_integration.entry_id
    )

    assert device is not None
    assert device.name == "Morayfield"
    assert device.manufacturer == "Planet Fitness Australia"
    assert device.model == "Club"
    assert device.configuration_url == MEMBER_PORTAL_URL
    devices = dr.async_entries_for_config_entry(registry, init_integration.entry_id)
    assert len(devices) == 4


async def test_entity_ids_follow_the_club_name(hass, init_integration) -> None:
    assert hass.states.get("sensor.mystery_club_reported_occupancy").state == "10"


async def test_setup_retries_while_the_portal_is_down(
    hass, mock_entry, portal, remote
) -> None:
    portal.go_down()

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    # One attempt plus the two retries.
    assert portal.fetches == 3


async def test_bad_credentials_start_reauthentication(
    hass, mock_entry, portal, remote
) -> None:
    portal.login_status = 401

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_retired_entities_are_removed(hass, mock_entry, portal, remote) -> None:
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "morayfield_next_change",
        config_entry=mock_entry,
        suggested_object_id="morayfield_next_change",
    )
    assert registry.async_get(stale.entity_id) is not None

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get("sensor.morayfield_reported_occupancy") is not None


async def test_device_removal_only_allowed_once_the_club_is_gone(
    hass, init_integration
) -> None:
    registry = dr.async_get(hass)
    present = registry.async_get_device_by_identifier(
        (DOMAIN, "morayfield"), init_integration.entry_id
    )
    ghost = registry.async_get_or_create(
        config_entry_id=init_integration.entry_id, identifiers={(DOMAIN, "ghost")}
    )

    assert not await async_remove_config_entry_device(hass, init_integration, present)
    assert await async_remove_config_entry_device(hass, init_integration, ghost)


async def test_a_club_that_vanishes_goes_unavailable(
    hass, init_integration, portal, freezer
) -> None:
    portal.clubs = [c for c in portal.clubs if c["name"] != "Eastside"]
    # The client caches the studio list for 6 hours, so a club opening or
    # closing is only noticed once that has passed.
    await refresh(hass, freezer, minutes=7 * 60)

    coordinator = init_integration.runtime_data
    assert "eastside" not in coordinator.estimates
    assert "eastside" not in coordinator.densities
    assert "eastside" not in coordinator.trends
    for entity in (
        "sensor.eastside_reported_occupancy",
        "sensor.eastside_real_occupancy",
        "sensor.eastside_busyness",
        "sensor.eastside_staffing_status",
        "binary_sensor.eastside_occupancy_trend",
    ):
        assert hass.states.get(entity).state == "unavailable"
    assert hass.states.get("sensor.morayfield_reported_occupancy").state == "40"


async def test_a_new_club_gets_entities_without_a_reload(
    hass, init_integration, portal, freezer
) -> None:
    assert hass.states.get("sensor.new_gym_reported_occupancy") is None

    portal.clubs.append(club_json("New Gym", 60))
    # The client caches the studio list for 6 hours, so a club opening or
    # closing is only noticed once that has passed.
    await refresh(hass, freezer, minutes=7 * 60)

    assert hass.states.get("sensor.new_gym_reported_occupancy").state == "60"
    assert hass.states.get("binary_sensor.new_gym_occupancy_trend") is not None
    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, "new_gym"), init_integration.entry_id
        )
        is not None
    )
