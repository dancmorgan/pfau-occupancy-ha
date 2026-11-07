"""Diagnostics for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import PlanetFitnessConfigEntry

# The entry's title and unique_id are both the member's email address.
TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlanetFitnessConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with credentials redacted."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "clubs": {key: asdict(club) for key, club in coordinator.data.items()},
        "estimates": {
            key: asdict(estimate) for key, estimate in coordinator.estimates.items()
        },
        "profiles_loaded": sorted(coordinator.profiles),
    }
