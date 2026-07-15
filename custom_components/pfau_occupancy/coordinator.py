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

from .const import (
    CONF_REDUCTION_PERCENT,
    DEFAULT_REDUCTION_PERCENT,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .estimator import ClubEstimate, estimate_occupancy

_LOGGER = logging.getLogger(__name__)

type PlanetFitnessConfigEntry = ConfigEntry["PlanetFitnessCoordinator"]


class PlanetFitnessCoordinator(DataUpdateCoordinator[dict[str, Club]]):
    """Fetches club occupancy, keyed by the club's slug.

    Alongside the raw counts, derives an estimated real occupancy by applying
    a flat percentage reduction to the portal's reported count (see
    estimator.py for why).
    """

    config_entry: PlanetFitnessConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PlanetFitnessConfigEntry) -> None:
        scan_minutes = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        )
        _LOGGER.debug("Polling every %d minutes", scan_minutes)
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
        self.reduction_percent = int(
            entry.options.get(CONF_REDUCTION_PERCENT, DEFAULT_REDUCTION_PERCENT)
        )
        self.estimates: dict[str, ClubEstimate] = {}

    async def _async_update_data(self) -> dict[str, Club]:
        try:
            clubs = await self.client.async_get_clubs()
        except PlanetFitnessAuthError as err:
            _LOGGER.debug("Update failed with auth error: %s", err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlanetFitnessConnectionError as err:
            _LOGGER.debug("Update failed with connection error: %s", err)
            raise UpdateFailed(str(err)) from err
        _LOGGER.debug("Update fetched %d clubs", len(clubs))

        data = {club.key: club for club in clubs}

        # Drop estimates for clubs that vanished (renamed or removed).
        for key in list(self.estimates):
            if key not in data:
                del self.estimates[key]

        for key, club in data.items():
            if club.occupancy is None:
                # No raw count this poll; keep the last estimate rather than
                # feeding the model a fabricated zero.
                continue
            self.estimates[key] = estimate_occupancy(club.occupancy, self.reduction_percent)

        return data
