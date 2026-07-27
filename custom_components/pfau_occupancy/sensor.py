"""Sensor platform for Planet Fitness club occupancy."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from pfau_occupancy import Club

from .club_data import ClubProfile
from .const import DOMAIN
from .coordinator import PlanetFitnessConfigEntry, PlanetFitnessCoordinator
from .density import Busyness, DensityReading
from .estimator import ClubEstimate
from .hours import ClubSchedule, Staffing

# Fire a moment after a staffing boundary rather than exactly on it, so a
# timer that runs a hair early doesn't re-read the state it just left.
_TRANSITION_GRACE = timedelta(seconds=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlanetFitnessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up club occupancy sensors, adding new clubs as they appear."""
    coordinator = entry.runtime_data
    known_keys: set[str] = set()

    @callback
    def _add_new_clubs() -> None:
        new_keys = set(coordinator.data) - known_keys
        if not new_keys:
            return
        known_keys.update(new_keys)
        async_add_entities(
            [
                *(PlanetFitnessReportedSensor(coordinator, key) for key in new_keys),
                *(PlanetFitnessRealSensor(coordinator, key) for key in new_keys),
                *(PlanetFitnessStaffingSensor(coordinator, key) for key in new_keys),
                *(PlanetFitnessFloorAreaSensor(coordinator, key) for key in new_keys),
                *(PlanetFitnessBusynessSensor(coordinator, key) for key in new_keys),
            ]
        )

    _add_new_clubs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clubs))


class PlanetFitnessClubSensorBase(
    CoordinatorEntity[PlanetFitnessCoordinator], SensorEntity
):
    """Shared behavior for the per-club sensors.

    Identity is the slugified club name (the API exposes no club ID). If a club
    disappears from a poll response (renamed, or temporarily dropped), `_club`
    resolves to None and `available` goes False rather than removing the entity.

    Each club is its own Device, grouping its five sensors under one card
    instead of a flat list — worth the onboarding friction of HA prompting to
    name/assign-area a new device per club on first setup. `has_entity_name`
    means each subclass's `_attr_name` is just the entity's own short name
    ("Reported Occupancy"); the device name ("Morayfield") is prepended by
    the frontend.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator)
        self._club_key = club_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, club_key)},
            name=coordinator.data[club_key].name.title(),
            manufacturer="Planet Fitness Australia",
        )

    @property
    def _club(self) -> Club | None:
        return self.coordinator.data.get(self._club_key)

    @property
    def _profile(self) -> ClubProfile | None:
        return self.coordinator.profile(self._club_key)

    @property
    def available(self) -> bool:
        return super().available and self._club is not None


class PlanetFitnessOccupancySensorBase(PlanetFitnessClubSensorBase):
    """Shared presentation for the two headcount sensors."""

    _attr_native_unit_of_measurement = "people"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-group"


class PlanetFitnessReportedSensor(PlanetFitnessOccupancySensorBase):
    """The portal's raw counter, exposed verbatim.

    Not a true headcount: the portal adds 1 per member scan and removes it on
    a fixed timer (the counter window), so this is a trailing sum of arrivals.
    """

    _attr_name = "Reported Occupancy"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_occupancy"

    @property
    def native_value(self) -> int | None:
        club = self._club
        return club.occupancy if club else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        club = self._club
        if club is None:
            return {}
        return {
            "address": club.address,
            "limit": club.limit,
            "percent_full": club.percent_full,
        }


class PlanetFitnessRealSensor(PlanetFitnessOccupancySensorBase):
    """Estimated real occupancy, derived from the reported counter.

    The coordinator applies a flat percentage reduction to the portal's
    reported count (see estimator.py for why).
    """

    _attr_name = "Real Occupancy"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_estimated"

    @property
    def _estimate(self) -> ClubEstimate | None:
        return self.coordinator.estimates.get(self._club_key)

    @property
    def available(self) -> bool:
        return super().available and self._estimate is not None

    @property
    def native_value(self) -> int | None:
        estimate = self._estimate
        return estimate.estimated_occupancy if estimate else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        estimate = self._estimate
        if estimate is None:
            return {}
        return {
            "raw_count": estimate.raw_count,
            "reduction_percent": self.coordinator.reduction_percent,
        }


class PlanetFitnessStaffingSensor(PlanetFitnessClubSensorBase):
    """Where the club is on its opening-hours timeline: staffed, unstaffed, closed.

    Unlike every other sensor here this doesn't move with the poll — it moves
    with the clock — so it schedules its own update for the next boundary in
    the club's weekly schedule instead of waiting for the coordinator.

    Unavailable for clubs with no `open`/`staffed` hours in clubs.yaml, since
    guessing 24/7-and-never-staffed would be indistinguishable from a real
    answer.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in Staffing]
    _attr_translation_key = "staffing"
    _attr_icon = "mdi:clock-outline"
    _attr_name = "Staffing"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_staffing"
        self._unsub_transition: CALLBACK_TYPE | None = None

    @property
    def _schedule(self) -> ClubSchedule | None:
        profile = self._profile
        return profile.schedule if profile else None

    def _now(self) -> datetime:
        return dt_util.utcnow().astimezone(self.coordinator.timezone(self._club_key))

    @property
    def available(self) -> bool:
        return super().available and self._schedule is not None

    @property
    def native_value(self) -> str | None:
        schedule = self._schedule
        return schedule.state_at(self._now()).value if schedule else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        schedule = self._schedule
        if schedule is None:
            return {}
        now = self._now()
        next_change = schedule.next_change(now)
        return {
            "next_change": next_change,
            "next_state": (
                schedule.state_at(next_change).value
                if next_change is not None
                else None
            ),
            "staffed_today": schedule.staffed_text_for(now.date()),
            "staffed_tomorrow": schedule.staffed_text_for(
                (now + timedelta(days=1)).date()
            ),
            "timezone": str(self.coordinator.timezone(self._club_key)),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_transition()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_transition()
        await super().async_will_remove_from_hass()

    @callback
    def _schedule_transition(self) -> None:
        """Arm a one-shot timer for the club's next staffing boundary."""
        self._cancel_transition()
        schedule = self._schedule
        if schedule is None:
            return
        next_change = schedule.next_change(self._now())
        if next_change is None:
            # A schedule with no transitions (e.g. open 24/7, never staffed)
            # holds one state forever; nothing to wake up for.
            return
        self._unsub_transition = async_track_point_in_time(
            self.hass, self._handle_transition, next_change + _TRANSITION_GRACE
        )

    @callback
    def _cancel_transition(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None

    @callback
    def _handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        self.async_write_ha_state()
        self._schedule_transition()

    @callback
    def _handle_coordinator_update(self) -> None:
        # A poll can't change the schedule, but it can be the first one to
        # produce this club — re-arm in case the timer had nothing to work
        # with earlier.
        super()._handle_coordinator_update()
        if self._unsub_transition is None:
            self._schedule_transition()


class PlanetFitnessFloorAreaSensor(PlanetFitnessClubSensorBase):
    """The club's usable gym floor area, from clubs.yaml.

    Static configuration rather than a measurement, so it's filed under
    diagnostics — it exists to make the number behind the Busyness sensor
    visible and templatable, not to be graphed.
    """

    _attr_native_unit_of_measurement = "m²"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:floor-plan"
    _attr_name = "Floor Area"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_floor_area"

    @property
    def _area(self) -> float | None:
        profile = self._profile
        return profile.area_sqm if profile else None

    @property
    def available(self) -> bool:
        return super().available and self._area is not None

    @property
    def native_value(self) -> float | None:
        return self._area


class PlanetFitnessBusynessSensor(PlanetFitnessClubSensorBase):
    """How crowded the club is: quiet, busy, or crowded.

    Estimated real occupancy per 16 square metres of effective floor area
    (configured area_sqm with dead space subtracted — see density.py), banded
    by the thresholds from the integration options (globally, or overridden
    for this club — see coordinator.thresholds). Needs an `area_sqm` for the
    club, so it stays unavailable until one is set.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [band.value for band in Busyness]
    _attr_translation_key = "busyness"
    _attr_icon = "mdi:gauge"
    _attr_name = "Busyness"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_busyness"

    @property
    def _density(self) -> DensityReading | None:
        return self.coordinator.densities.get(self._club_key)

    @property
    def available(self) -> bool:
        return super().available and self._density is not None

    @property
    def native_value(self) -> str | None:
        density = self._density
        return density.band.value if density else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        density = self._density
        if density is None:
            return {}
        busy, crowded = self.coordinator.thresholds(self._club_key)
        return {
            "people_per_16sqm": density.people_per_16sqm,
            "sqm_per_person": density.sqm_per_person,
            "people": density.people,
            "area_sqm": density.area_sqm,
            "effective_area_sqm": density.effective_area_sqm,
            "busy_threshold": busy,
            "crowded_threshold": crowded,
        }
