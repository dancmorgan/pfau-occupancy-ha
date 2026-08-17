"""Binary sensor platform: which way each club's occupancy is heading."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TREND_MIN_GRADIENT
from .coordinator import PlanetFitnessConfigEntry, PlanetFitnessCoordinator
from .entity import PlanetFitnessClubEntity
from .trend import DEFAULT_TREND, Trend, TrendReading


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlanetFitnessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one occupancy-trend sensor per club, as clubs appear."""
    coordinator = entry.runtime_data
    known_keys: set[str] = set()

    @callback
    def _add_new_clubs() -> None:
        new_keys = set(coordinator.data) - known_keys
        if not new_keys:
            return
        known_keys.update(new_keys)
        async_add_entities(
            PlanetFitnessOccupancyTrendSensor(coordinator, key) for key in new_keys
        )

    _add_new_clubs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clubs))


class PlanetFitnessOccupancyTrendSensor(PlanetFitnessClubEntity, BinarySensorEntity):
    """Whether the club is filling up or emptying out.

    `on` is getting busier, `off` is getting quieter — the gradient of a
    least-squares line through the Real Occupancy samples in a rolling
    window, not a comparison of the last two polls, which would be far too
    jumpy to act on. See trend.py for the model and its deadband.

    Deliberately carries no device class: the stock `running`/`problem`
    classes would relabel the states in the frontend, and none of them mean
    "busier vs quieter". The names come from this integration's own
    translations instead.

    Never `unknown`. The samples are in-memory only, so a restart empties the
    window, and rather than go stateless the sensor falls back to "getting
    busier" until a line can be fitted again — see trend.DEFAULT_TREND. The
    `established` attribute is what tells the two apart.
    """

    _attr_translation_key = "occupancy_trend"
    _attr_name = "Occupancy Trend"

    def __init__(self, coordinator: PlanetFitnessCoordinator, club_key: str) -> None:
        super().__init__(coordinator, club_key)
        self._attr_unique_id = f"{club_key}_occupancy_trend"

    @property
    def _trend(self) -> TrendReading | None:
        return self.coordinator.trends.get(self._club_key)

    @property
    def _direction(self) -> Trend:
        """This club's direction, or the default before any poll has landed."""
        trend = self._trend
        return trend.direction if trend is not None else DEFAULT_TREND

    @property
    def is_on(self) -> bool:
        return self._direction is Trend.RISING

    @property
    def icon(self) -> str:
        return "mdi:trending-up" if self.is_on else "mdi:trending-down"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        trend = self._trend
        if trend is None:
            return {"established": False, "samples": 0}
        return {
            # False means the state is the assumed default, not a measurement
            # — the window hasn't refilled since the last restart.
            "established": trend.established,
            # Signed, so a held direction is still distinguishable from a
            # genuine one: a positive gradient under the deadband while the
            # state reads "getting quieter" means it's coasting, not climbing.
            "gradient_people_per_hour": trend.gradient,
            "deadband_people_per_hour": TREND_MIN_GRADIENT,
            "change_over_window": trend.change,
            "window_minutes": round(trend.span_seconds / 60),
            "configured_window_minutes": self.coordinator.trend_window_minutes,
            "samples": trend.samples,
        }
