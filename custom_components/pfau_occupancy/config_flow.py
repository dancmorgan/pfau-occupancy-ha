"""Config flow for the Planet Fitness AU Occupancy integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from pfau_occupancy import (
    PlanetFitnessAuthError,
    PlanetFitnessClient,
    PlanetFitnessConnectionError,
)

from .const import (
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
)

CONF_CLUB = "club"

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class PlanetFitnessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Planet Fitness AU Occupancy."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def _validate(self, email: str, password: str) -> dict[str, str]:
        """Try a real login+fetch against the portal; return errors, if any."""
        client = PlanetFitnessClient(
            email, password, session=async_get_clientsession(self.hass)
        )
        try:
            await client.async_get_clubs()
        except PlanetFitnessAuthError:
            return {"base": "invalid_auth"}
        except PlanetFitnessConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Planet Fitness credentials")
            return {"base": "unknown"}
        return {}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> PlanetFitnessOptionsFlow:
        """Create the options flow."""
        return PlanetFitnessOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()
            errors = await self._validate(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )

        # Re-showing the form with the previous input as suggested values keeps
        # the email filled in after a failed attempt (passwords are never
        # round-tripped back into the form by HA).
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication triggered by ConfigEntryAuthFailed."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and re-validate."""
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._validate(
                self._reauth_entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"email": self._reauth_entry.data[CONF_EMAIL]},
        )


def _minutes_box(min_value: int, max_value: int) -> vol.All:
    """A plain number-box selector for a minutes field.

    Required + BOX mode so the frontend renders neither an enable checkbox
    nor a slider.
    """
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=min_value,
                max=max_value,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Coerce(int),
    )


def _percent_box(min_value: int, max_value: int) -> vol.All:
    """A plain number-box selector for a percentage field."""
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=min_value,
                max=max_value,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="%",
            )
        ),
        vol.Coerce(int),
    )


def _density_box() -> vol.All:
    """A number-box selector for a people-per-36-square-metre threshold.

    Fine steps because the useful range is small: one person per 20 m2 is
    1.8, one per 10 m2 is 3.6.
    """
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=0.01,
                max=36,
                step=0.01,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="people/36m²",
            )
        ),
        vol.Coerce(float),
    )


class PlanetFitnessOptionsFlow(OptionsFlow):
    """Options flow: poll interval, occupancy reduction, crowding thresholds.

    Everything here is a subjective, personal-feel setting — what counts as
    "busy", how much to discount the reported count — as opposed to facts
    about a club (its floor area, its hours), which come from clubs.yaml and
    are never user-configurable; see club_data.py for why. That split is why
    this is a menu: general settings apply to every club, and a club's own
    threshold override is a separate per-club step.
    """

    def __init__(self) -> None:
        self._club_key: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Land on a menu: general settings, or one club's threshold override."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "club_thresholds"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the scan interval, reduction, and global crowding thresholds."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Inverted thresholds would make "busy" unreachable, so reject
            # them here rather than silently producing a two-state sensor.
            if user_input[CONF_CROWDED_THRESHOLD] < user_input[CONF_BUSY_THRESHOLD]:
                errors[CONF_CROWDED_THRESHOLD] = "thresholds_inverted"
            else:
                return self.async_create_entry(
                    data={**self.config_entry.options, **user_input}
                )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): _minutes_box(1, 60),
                vol.Required(
                    CONF_REDUCTION_PERCENT,
                    default=options.get(
                        CONF_REDUCTION_PERCENT, DEFAULT_REDUCTION_PERCENT
                    ),
                ): _percent_box(0, 90),
                vol.Required(
                    CONF_BUSY_THRESHOLD,
                    default=options.get(CONF_BUSY_THRESHOLD, DEFAULT_BUSY_THRESHOLD),
                ): _density_box(),
                vol.Required(
                    CONF_CROWDED_THRESHOLD,
                    default=options.get(
                        CONF_CROWDED_THRESHOLD, DEFAULT_CROWDED_THRESHOLD
                    ),
                ): _density_box(),
                vol.Required(
                    CONF_TREND_WINDOW_MINUTES,
                    default=options.get(
                        CONF_TREND_WINDOW_MINUTES, DEFAULT_TREND_WINDOW_MINUTES
                    ),
                ): _minutes_box(5, 240),
            }
        )
        return self.async_show_form(
            step_id="general",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_club_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which discovered club to set a threshold override for."""
        coordinator = self.config_entry.runtime_data
        clubs = coordinator.data if coordinator is not None else {}
        if not clubs:
            return self.async_abort(reason="no_clubs_discovered")

        if user_input is not None:
            self._club_key = user_input[CONF_CLUB]
            return await self.async_step_club_threshold_values()

        schema = vol.Schema(
            {
                vol.Required(CONF_CLUB): SelectSelector(
                    SelectSelectorConfig(
                        mode=SelectSelectorMode.DROPDOWN,
                        options=[
                            {"value": key, "label": club.name.title()}
                            for key, club in sorted(
                                clubs.items(), key=lambda item: item[1].name
                            )
                        ],
                    )
                ),
            }
        )
        return self.async_show_form(step_id="club_thresholds", data_schema=schema)

    async def async_step_club_threshold_values(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set (or clear) one club's busy/crowded override.

        Leaving both fields blank clears the override, falling back to the
        general thresholds. Setting only one is rejected — a club's pair is
        all-or-nothing, same rule as the general step.
        """
        club_key = self._club_key
        assert club_key is not None
        coordinator = self.config_entry.runtime_data
        club_name = coordinator.data[club_key].name.title()

        errors: dict[str, str] = {}
        if user_input is not None:
            busy = user_input.get(CONF_BUSY_THRESHOLD)
            crowded = user_input.get(CONF_CROWDED_THRESHOLD)
            if (busy is None) != (crowded is None):
                errors["base"] = "club_threshold_partial"
            elif busy is not None and crowded < busy:
                errors[CONF_CROWDED_THRESHOLD] = "thresholds_inverted"
            else:
                overrides = dict(
                    self.config_entry.options.get(CONF_CLUB_THRESHOLDS, {})
                )
                if busy is None:
                    overrides.pop(club_key, None)
                else:
                    overrides[club_key] = {
                        CONF_BUSY_THRESHOLD: busy,
                        CONF_CROWDED_THRESHOLD: crowded,
                    }
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        CONF_CLUB_THRESHOLDS: overrides,
                    }
                )

        # No `default=` here: an Optional field with a default is never
        # actually absent, which would break "leave both blank to clear".
        schema = vol.Schema(
            {
                vol.Optional(CONF_BUSY_THRESHOLD): _density_box(),
                vol.Optional(CONF_CROWDED_THRESHOLD): _density_box(),
            }
        )
        # Re-show what was just (invalidly) submitted on error, same as the
        # general step; otherwise prefill with this club's existing override.
        if user_input is None:
            user_input = self.config_entry.options.get(CONF_CLUB_THRESHOLDS, {}).get(
                club_key, {}
            )
        return self.async_show_form(
            step_id="club_threshold_values",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={"club": club_name},
        )
