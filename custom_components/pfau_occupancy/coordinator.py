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
from homeassistant.util import dt as dt_util

from pfau_occupancy import (
    Club,
    PlanetFitnessAuthError,
    PlanetFitnessClient,
    PlanetFitnessConnectionError,
)

from .const import (
    CONF_COUNTER_WINDOW,
    CONF_REAL_DWELL,
    DEFAULT_COUNTER_WINDOW_MINUTES,
    DEFAULT_REAL_DWELL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .estimator import ClubEstimate, OccupancyEstimator

_LOGGER = logging.getLogger(__name__)

type PlanetFitnessConfigEntry = ConfigEntry["PlanetFitnessCoordinator"]


class PlanetFitnessCoordinator(DataUpdateCoordinator[dict[str, Club]]):
    """Fetches club occupancy, keyed by the club's slug.

    Alongside the raw counts, maintains a per-club OccupancyEstimator that
    reconstructs the arrival flow encoded in the portal's trailing-sum counter
    and derives an estimated real occupancy. Estimator history is in-memory
    only: after a restart (or an options change, which reloads the entry) each
    club re-seeds and warms up over roughly one counter window.
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
        self.counter_window_minutes = int(
            entry.options.get(CONF_COUNTER_WINDOW, DEFAULT_COUNTER_WINDOW_MINUTES)
        )
        self.real_dwell_minutes = int(
            entry.options.get(CONF_REAL_DWELL, DEFAULT_REAL_DWELL_MINUTES)
        )
        # Bucket size equals the poll interval, keeping the model's bucket
        # arithmetic in lockstep with how often data actually arrives.
        self._bucket_size = timedelta(minutes=scan_minutes)
        self._estimators: dict[str, OccupancyEstimator] = {}
        self.estimates: dict[str, ClubEstimate] = {}

    def _make_estimator(self) -> OccupancyEstimator:
        return OccupancyEstimator(
            counter_window=timedelta(minutes=self.counter_window_minutes),
            real_dwell=timedelta(minutes=self.real_dwell_minutes),
            bucket_size=self._bucket_size,
        )

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

        # Drop estimator state for clubs that vanished (renamed or removed).
        for key in list(self._estimators):
            if key not in data:
                del self._estimators[key]
                self.estimates.pop(key, None)

        now = dt_util.utcnow()
        for key, club in data.items():
            if club.occupancy is None:
                # No raw count this poll; keep the last estimate rather than
                # feeding the model a fabricated zero.
                continue
            estimator = self._estimators.setdefault(key, self._make_estimator())
            self.estimates[key] = estimator.update(club.occupancy, now)

        return data
