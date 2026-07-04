"""Data update coordinator for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pfau_occupancy import (
    Club,
    PlanetFitnessAuthError,
    PlanetFitnessClient,
    PlanetFitnessConnectionError,
)

from .const import DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)

type PlanetFitnessConfigEntry = ConfigEntry["PlanetFitnessCoordinator"]


class PlanetFitnessCoordinator(DataUpdateCoordinator[dict[str, Club]]):
    """Fetches club occupancy, keyed by the club's slug."""

    config_entry: PlanetFitnessConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> None:
        scan_minutes = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )
        self.client = PlanetFitnessClient(
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            session=async_get_clientsession(hass),
        )

    async def _async_update_data(self) -> dict[str, Club]:
        try:
            clubs = await self.client.async_get_clubs()
        except PlanetFitnessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlanetFitnessConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return {club.key: club for club in clubs}
