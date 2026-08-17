"""Data update coordinator for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import asyncio
import logging
import time
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
    CONF_TREND_WINDOW_MINUTES,
    DEFAULT_BUSY_THRESHOLD,
    DEFAULT_CROWDED_THRESHOLD,
    DEFAULT_REDUCTION_PERCENT,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_TREND_WINDOW_MINUTES,
    DOMAIN,
    TREND_MIN_GRADIENT,
    TREND_MIN_SAMPLES_IN_WINDOW,
)
from .density import DensityReading, measure_density, people_at_threshold
from .estimator import ClubEstimate, estimate_occupancy
from .trend import TrendReading, TrendSample, measure_trend, within_window

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
        # The configured window, but never narrower than several polls —
        # a 60-minute scan interval with a 15-minute window would otherwise
        # age every sample out before the next one landed.
        self.trend_window_minutes = max(
            int(
                entry.options.get(
                    CONF_TREND_WINDOW_MINUTES, DEFAULT_TREND_WINDOW_MINUTES
                )
            ),
            scan_minutes * TREND_MIN_SAMPLES_IN_WINDOW,
        )
        self.trend_window_seconds = self.trend_window_minutes * 60
        self.estimates: dict[str, ClubEstimate] = {}
        self.densities: dict[str, DensityReading] = {}
        self.trends: dict[str, TrendReading] = {}
        self._trend_samples: dict[str, list[TrendSample]] = {}
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

    def headcount_thresholds(self, club_key: str) -> tuple[int, int] | None:
        """This club's (busy, crowded) thresholds as headcounts, not densities.

        None for a club with no floor area, since people/36m2 can't be turned
        back into people without one. Ordered, because thresholds() is.
        """
        profile = self.profiles.get(club_key)
        if profile is None or profile.area_sqm is None:
            return None
        busy, crowded = self.thresholds(club_key)
        return (
            people_at_threshold(profile.area_sqm, busy),
            people_at_threshold(profile.area_sqm, crowded),
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
        self._log_missing_profiles(data)

        # Drop derived values for clubs that vanished (renamed or removed).
        for key in list(self.estimates):
            if key not in data:
                del self.estimates[key]
        for key in list(self.densities):
            if key not in data:
                del self.densities[key]
        for key in list(self.trends):
            if key not in data:
                del self.trends[key]
                self._trend_samples.pop(key, None)

        for key, club in data.items():
            if club.occupancy is None:
                # No raw count this poll; keep the last estimate rather than
                # feeding the model a fabricated zero.
                continue
            estimate = estimate_occupancy(club.occupancy, self.reduction_percent)
            self.estimates[key] = estimate
            self._update_density(key, estimate)
            self._update_trend(key, estimate)

        return data

    def _update_density(self, key: str, estimate: ClubEstimate) -> None:
        profile = self.profiles.get(key)
        if profile is None or profile.area_sqm is None:
            return
        busy, crowded = self.thresholds(key)
        self.densities[key] = measure_density(
            estimate.estimated_occupancy, profile.area_sqm, busy, crowded
        )

    def _update_trend(self, key: str, estimate: ClubEstimate) -> None:
        """Record this poll's estimate and re-fit the club's trend line.

        Samples are stamped with a monotonic clock because only the intervals
        between them matter, and wall time can step under NTP or DST. History
        lives in memory only, so a restart starts the window over and the
        trend is unknown until it refills.
        """
        now = time.monotonic()
        samples = self._trend_samples.get(key, [])
        samples.append(TrendSample(at=now, people=estimate.estimated_occupancy))
        samples = within_window(samples, now, self.trend_window_seconds)
        self._trend_samples[key] = samples

        previous = self.trends.get(key)
        self.trends[key] = (
            measure_trend(samples, TREND_MIN_GRADIENT, previous.direction)
            if previous is not None
            else measure_trend(samples, TREND_MIN_GRADIENT)
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
