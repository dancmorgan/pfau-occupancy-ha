"""Data update coordinator for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import logging
from datetime import timedelta, tzinfo
from pathlib import Path

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

from .club_data import (
    CLUB_DATA_FILENAME,
    ClubDataError,
    ClubProfile,
    load_club_profiles,
)
from .const import (
    CONF_BUSY_THRESHOLD,
    CONF_CROWDED_THRESHOLD,
    CONF_REDUCTION_PERCENT,
    DEFAULT_BUSY_THRESHOLD,
    DEFAULT_CROWDED_THRESHOLD,
    DEFAULT_REDUCTION_PERCENT,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .density import DensityReading, measure_density
from .estimator import ClubEstimate, estimate_occupancy

_LOGGER = logging.getLogger(__name__)

type PlanetFitnessConfigEntry = ConfigEntry["PlanetFitnessCoordinator"]


class PlanetFitnessCoordinator(DataUpdateCoordinator[dict[str, Club]]):
    """Fetches club occupancy, keyed by the club's slug.

    Alongside the raw counts, derives an estimated real occupancy by applying
    a flat percentage reduction to the portal's reported count (see
    estimator.py for why), and — for clubs with a floor area in clubs.yaml —
    how crowded that estimate makes the club.
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
        self.busy_threshold = float(
            entry.options.get(CONF_BUSY_THRESHOLD, DEFAULT_BUSY_THRESHOLD)
        )
        self.crowded_threshold = float(
            entry.options.get(CONF_CROWDED_THRESHOLD, DEFAULT_CROWDED_THRESHOLD)
        )
        self.estimates: dict[str, ClubEstimate] = {}
        self.densities: dict[str, DensityReading] = {}
        self.profiles: dict[str, ClubProfile] = {}
        self._timezones: dict[str, tzinfo] = {}
        self._warned_missing_profiles = False

    async def async_load_club_profiles(self) -> None:
        """Read clubs.yaml off the event loop, before the first refresh.

        A broken file must not take the integration down: the occupancy
        sensors don't depend on it, so the error is logged and the affected
        sensors stay unavailable until it's fixed.
        """
        path = Path(__file__).parent / CLUB_DATA_FILENAME
        try:
            self.profiles = await self.hass.async_add_executor_job(
                load_club_profiles, path
            )
        except ClubDataError as err:
            _LOGGER.error(
                "Ignoring club data: %s. Floor area and staffed hours will be "
                "unavailable until this is corrected",
                err,
            )
            self.profiles = {}
        else:
            _LOGGER.debug("Loaded profiles for %d club(s)", len(self.profiles))
        await self._async_resolve_timezones()

    async def _async_resolve_timezones(self) -> None:
        """Turn the profiles' IANA names into tzinfo objects up front.

        Loading a zone touches the filesystem, so it's done here rather than
        on demand from an entity property on the event loop.
        """
        self._timezones = {}
        for key, profile in self.profiles.items():
            if profile.timezone is None:
                continue
            zone = await dt_util.async_get_time_zone(profile.timezone)
            if zone is None:
                _LOGGER.warning(
                    "Unknown timezone %r for club %s; falling back to Home "
                    "Assistant's timezone",
                    profile.timezone,
                    key,
                )
                continue
            self._timezones[key] = zone

    def profile(self, club_key: str) -> ClubProfile | None:
        """The static profile for a club, if clubs.yaml has one."""
        return self.profiles.get(club_key)

    def timezone(self, club_key: str) -> tzinfo:
        """The club's local timezone, defaulting to Home Assistant's own."""
        return self._timezones.get(club_key) or dt_util.DEFAULT_TIME_ZONE

    def thresholds(self, club_key: str) -> tuple[float, float]:
        """This club's (busy, crowded) thresholds, honouring any override."""
        profile = self.profiles.get(club_key)
        if profile is None:
            return self.busy_threshold, self.crowded_threshold
        busy = (
            self.busy_threshold
            if profile.busy_threshold is None
            else profile.busy_threshold
        )
        crowded = (
            self.crowded_threshold
            if profile.crowded_threshold is None
            else profile.crowded_threshold
        )
        # clubs.yaml rejects an inverted pair, and so does the options flow,
        # but overriding only one of the two can still cross them. Keep the
        # bands ordered so "busy" stays reachable.
        return busy, max(busy, crowded)

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
        self._log_missing_profiles(data)

        # Drop derived values for clubs that vanished (renamed or removed).
        for key in list(self.estimates):
            if key not in data:
                del self.estimates[key]
        for key in list(self.densities):
            if key not in data:
                del self.densities[key]

        for key, club in data.items():
            if club.occupancy is None:
                # No raw count this poll; keep the last estimate rather than
                # feeding the model a fabricated zero.
                continue
            estimate = estimate_occupancy(club.occupancy, self.reduction_percent)
            self.estimates[key] = estimate
            self._update_density(key, estimate)

        return data

    def _update_density(self, key: str, estimate: ClubEstimate) -> None:
        profile = self.profiles.get(key)
        if profile is None or profile.area_sqm is None:
            return
        busy, crowded = self.thresholds(key)
        self.densities[key] = measure_density(
            estimate.estimated_occupancy, profile.area_sqm, busy, crowded
        )

    def _log_missing_profiles(self, data: dict[str, Club]) -> None:
        """Name the clubs with no clubs.yaml entry, so the keys are discoverable.

        Once per reload rather than once per poll — with the full club list
        coming back every time, this would otherwise be relentless.
        """
        if self._warned_missing_profiles or not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        self._warned_missing_profiles = True
        missing = sorted(set(data) - set(self.profiles))
        if missing:
            _LOGGER.debug(
                "No %s entry for %d club(s): %s",
                CLUB_DATA_FILENAME,
                len(missing),
                ", ".join(missing),
            )
