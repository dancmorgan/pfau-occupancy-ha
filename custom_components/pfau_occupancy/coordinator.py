"""Data update coordinator for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
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
    parse_club_data,
)
from .const import (
    CLUB_DATA_CACHE_FILENAME,
    CLUB_DATA_REFRESH_HOURS,
    CLUB_DATA_URL,
    CONF_BUSY_THRESHOLD,
    CONF_CLUB_THRESHOLDS,
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

_CLUB_DATA_FETCH_TIMEOUT = 10

type PlanetFitnessConfigEntry = ConfigEntry["PlanetFitnessCoordinator"]


class PlanetFitnessCoordinator(DataUpdateCoordinator[dict[str, Club]]):
    """Fetches club occupancy, keyed by the club's slug.

    Alongside the raw counts, derives an estimated real occupancy by applying
    a flat percentage reduction to the portal's reported count (see
    estimator.py for why), and — for clubs with a floor area in clubs.yaml —
    how crowded that estimate makes the club.

    Club facts (area_sqm, hours) are loaded separately from occupancy: see
    async_load_club_profiles for how they're fetched from GitHub, cached, and
    falling back to the bundled copy. Crowding thresholds are a different,
    user-configurable thing — see thresholds().
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
        """(Re)load club profiles: fetched from GitHub, cache, or bundled fallback.

        Never raises — every layer is independently fault-tolerant, so a
        fetch failure falls through to the cache, and a cache failure falls
        through to the copy shipped with this release. Safe to call again
        later to pick up a fresh fetch; call `async_start_club_data_refresh`
        to do that on a timer.
        """
        self.profiles = await self._async_load_base_club_data()
        _LOGGER.debug("Loaded profiles for %d club(s)", len(self.profiles))
        await self._async_resolve_timezones()

    @callback
    def async_start_club_data_refresh(self) -> CALLBACK_TYPE:
        """Start periodically refetching clubs.yaml, so a running instance picks
        up repo changes without a restart. Returns an unsub callback.
        """
        return async_track_time_interval(
            self.hass,
            self._async_refresh_club_data,
            timedelta(hours=CLUB_DATA_REFRESH_HOURS),
        )

    async def _async_refresh_club_data(self, _now: datetime) -> None:
        await self.async_load_club_profiles()
        self.async_update_listeners()

    async def _async_load_base_club_data(self) -> dict[str, ClubProfile]:
        """The fetched-from-GitHub base data, falling back to cache then bundled."""
        remote_text = await self._async_fetch_remote_club_data()
        if remote_text is not None:
            try:
                profiles = parse_club_data(remote_text, "remote clubs.yaml")
            except ClubDataError as err:
                _LOGGER.warning("Ignoring malformed remote clubs.yaml: %s", err)
            else:
                await self._async_write_club_data_cache(remote_text)
                return profiles

        cached_text = await self._async_read_club_data_cache()
        if cached_text is not None:
            try:
                profiles = parse_club_data(cached_text, "cached clubs.yaml")
            except ClubDataError as err:
                _LOGGER.warning("Ignoring malformed cached clubs.yaml: %s", err)
            else:
                _LOGGER.debug("Using cached club data; remote fetch unavailable")
                return profiles

        path = Path(__file__).parent / CLUB_DATA_FILENAME
        try:
            return await self.hass.async_add_executor_job(load_club_profiles, path)
        except ClubDataError as err:
            _LOGGER.error(
                "Ignoring bundled club data: %s. Floor area and staffed hours "
                "will be unavailable until this is corrected",
                err,
            )
            return {}

    async def _async_fetch_remote_club_data(self) -> str | None:
        """Best-effort fetch of the latest clubs.yaml from GitHub.

        Never raises — any network problem just falls through to the cache or
        bundled copy instead of failing the whole profile load.
        """
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(_CLUB_DATA_FETCH_TIMEOUT):
                response = await session.get(CLUB_DATA_URL)
                response.raise_for_status()
                return await response.text()
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug("Could not fetch %s: %s", CLUB_DATA_URL, err)
            return None

    async def _async_write_club_data_cache(self, text: str) -> None:
        path = Path(self.hass.config.path(CLUB_DATA_CACHE_FILENAME))
        try:
            await self.hass.async_add_executor_job(path.write_text, text, "utf-8")
        except OSError as err:
            _LOGGER.debug("Could not cache club data to %s: %s", path, err)

    async def _async_read_club_data_cache(self) -> str | None:
        path = Path(self.hass.config.path(CLUB_DATA_CACHE_FILENAME))
        if not path.is_file():
            return None
        try:
            return await self.hass.async_add_executor_job(path.read_text, "utf-8")
        except OSError as err:
            _LOGGER.debug("Could not read cached club data at %s: %s", path, err)
            return None

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
        """This club's (busy, crowded) thresholds, honouring any GUI override.

        Per-club overrides live in entry.options, set via the options flow's
        club_threshold_values step — always written as a matched busy/crowded
        pair (see config_flow.py), never partial.
        """
        override = self.config_entry.options.get(CONF_CLUB_THRESHOLDS, {}).get(
            club_key
        )
        if override is None:
            return self.busy_threshold, self.crowded_threshold
        busy = override[CONF_BUSY_THRESHOLD]
        crowded = override[CONF_CROWDED_THRESHOLD]
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
