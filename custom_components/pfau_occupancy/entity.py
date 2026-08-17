"""Shared base for every per-club entity, whatever platform it's on."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pfau_occupancy import Club

from .club_data import ClubProfile
from .const import DOMAIN
from .coordinator import PlanetFitnessCoordinator


class PlanetFitnessClubEntity(CoordinatorEntity[PlanetFitnessCoordinator]):
    """Identity, device grouping and availability for one club's entities.

    Identity is the slugified club name (the API exposes no club ID). If a club
    disappears from a poll response (renamed, or temporarily dropped), `_club`
    resolves to None and `available` goes False rather than removing the entity.

    Each club is its own Device, grouping its entities under one card instead
    of a flat list — worth the onboarding friction of HA prompting to
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
