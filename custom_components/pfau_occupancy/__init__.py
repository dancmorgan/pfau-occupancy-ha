"""The Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import PlanetFitnessConfigEntry, PlanetFitnessCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "sensor"]

# unique_id suffixes for sensors a past release created that this one no
# longer does. Cleaned up on every setup so an upgrading user isn't left with
# permanently-unavailable orphaned entities — nothing will ever write a
# state for them again. Append to this whenever a sensor is retired; safe to
# run repeatedly since there's nothing left to remove once they're gone.
_RETIRED_UNIQUE_ID_SUFFIXES = (
    "_next_change",
    "_next_state",
    "_next_staffed",
    "_next_unstaffed",
)


async def async_setup_entry(hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> bool:
    """Set up Planet Fitness AU Occupancy from a config entry."""
    _async_remove_retired_entities(hass, entry)

    coordinator = PlanetFitnessCoordinator(hass, entry)
    # Profiles must be in place before the first refresh, so the first poll can
    # already derive density for clubs that have a floor area.
    await coordinator.async_load_club_profiles()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Refetches clubs.yaml from the repo periodically, so club data can be
    # updated without every user pulling a new release (see coordinator.py).
    entry.async_on_unload(coordinator.async_start_club_data_refresh())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _async_remove_retired_entities(hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> None:
    """Remove any entity this integration registered under a unique_id that
    no current sensor produces — see _RETIRED_UNIQUE_ID_SUFFIXES.
    """
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id.endswith(_RETIRED_UNIQUE_ID_SUFFIXES):
            _LOGGER.debug("Removing retired entity %s", entity_entry.entity_id)
            registry.async_remove(entity_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PlanetFitnessConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Let the user delete a club's device once the portal no longer lists it.

    A club can drop out of a poll temporarily, so devices are never removed
    automatically; this only permits a manual removal for clubs that are absent
    from the latest data.
    """
    current = entry.runtime_data.data
    return not any(
        domain == DOMAIN and club_key in current
        for domain, club_key in device_entry.identifiers
    )


async def _async_update_listener(hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> None:
    """Reload the entry when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
