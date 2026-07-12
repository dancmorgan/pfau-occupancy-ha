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
)

from pfau_occupancy import (
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


class PlanetFitnessOptionsFlow(OptionsFlow):
    """Options flow: poll interval plus the occupancy-model constants."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the scan interval and estimator windows, in minutes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_REAL_DWELL] > user_input[CONF_COUNTER_WINDOW]:
                errors["base"] = "dwell_exceeds_window"
            else:
                return self.async_create_entry(data=user_input)

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
                    CONF_COUNTER_WINDOW,
                    default=options.get(
                        CONF_COUNTER_WINDOW, DEFAULT_COUNTER_WINDOW_MINUTES
                    ),
                ): _minutes_box(5, 720),
                vol.Required(
                    CONF_REAL_DWELL,
                    default=options.get(CONF_REAL_DWELL, DEFAULT_REAL_DWELL_MINUTES),
                ): _minutes_box(5, 720),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
