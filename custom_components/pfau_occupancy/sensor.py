"""Sensor platform for Planet Fitness club occupancy."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pfau_occupancy import Club

from .coordinator import PlanetFitnessConfigEntry, PlanetFitnessCoordinator


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
            PlanetFitnessClubSensor(coordinator, key) for key in new_keys
        )

    _add_new_clubs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clubs))


class PlanetFitnessClubSensor(CoordinatorEntity[PlanetFitnessCoordinator], SensorEntity):
    """A single club's current occupancy.

    Identity is the slugified club name (the API exposes no club ID). If a club
    disappears from a poll response (renamed, or temporarily dropped), `_club`
    resolves to None and `available` goes False rather than removing the entity.

    Deliberately not attached to a Device: a device per club would trigger HA's
    bulk "name and assign area" onboarding prompt for every club on first setup,
    which isn't wanted here. These are plain, ungrouped sensor entities.
    """

    _attr_native_unit_of_measurement = "people"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator)
        self._club_key = club_key
        self._attr_unique_id = f"{club_key}_occupancy"
        self._attr_name = f"{coordinator.data[club_key].name} Occupancy"

    @property
    def _club(self) -> Club | None:
        return self.coordinator.data.get(self._club_key)

    @property
    def available(self) -> bool:
        return super().available and self._club is not None

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
