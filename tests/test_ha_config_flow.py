"""Config flow, reauthentication and options flow."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType

from custom_components.pfau_occupancy.config_flow import CONF_CLUB
from custom_components.pfau_occupancy.const import (
    CONF_BUSY_THRESHOLD,
    CONF_CLUB_THRESHOLDS,
    CONF_CROWDED_THRESHOLD,
    CONF_REDUCTION_PERCENT,
    CONF_TREND_WINDOW_MINUTES,
    DOMAIN,
)
from ha_support import EMAIL, PASSWORD

USER_INPUT = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


class TestUserFlow:
    async def test_creates_entry(self, hass, portal) -> None:
        result = await _start_user_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        with patch(
            "custom_components.pfau_occupancy.async_setup_entry", return_value=True
        ) as setup:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
            await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == EMAIL
        assert result["data"] == USER_INPUT
        assert result["result"].unique_id == EMAIL.lower()
        setup.assert_called_once()

    @pytest.mark.parametrize(
        ("login_status", "error"),
        [(401, "invalid_auth"), (500, "cannot_connect")],
    )
    async def test_portal_errors_then_recovery(
        self, hass, portal, login_status: int, error: str
    ) -> None:
        portal.login_status = login_status
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error}

        portal.login_status = 200
        with patch(
            "custom_components.pfau_occupancy.async_setup_entry", return_value=True
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_unexpected_error(self, hass, portal) -> None:
        result = await _start_user_flow(hass)
        with patch(
            "custom_components.pfau_occupancy.config_flow.PlanetFitnessClient"
            ".async_get_clubs",
            side_effect=RuntimeError("boom"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}

    async def test_same_account_cannot_be_added_twice(
        self, hass, mock_entry, portal
    ) -> None:
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: EMAIL.upper(), CONF_PASSWORD: PASSWORD},
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestReauth:
    async def test_new_password_is_saved(self, hass, init_integration, portal) -> None:
        result = await init_integration.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["description_placeholders"]["email"] == EMAIL

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert init_integration.data[CONF_PASSWORD] == "new-password"
        assert init_integration.data[CONF_EMAIL] == EMAIL
        assert init_integration.state is ConfigEntryState.LOADED

    async def test_wrong_password_is_rejected(
        self, hass, init_integration, portal
    ) -> None:
        result = await init_integration.start_reauth_flow(hass)
        portal.login_status = 401
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}
        assert init_integration.data[CONF_PASSWORD] == PASSWORD


GENERAL_OPTIONS = {
    CONF_SCAN_INTERVAL: 10,
    CONF_REDUCTION_PERCENT: 40,
    CONF_BUSY_THRESHOLD: 1.5,
    CONF_CROWDED_THRESHOLD: 2.5,
    CONF_TREND_WINDOW_MINUTES: 90,
}


async def _open_options(hass, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"general", "club_thresholds"}
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


class TestGeneralOptions:
    async def test_saved_and_applied(self, hass, init_integration) -> None:
        result = await _open_options(hass, init_integration, "general")
        assert result["step_id"] == "general"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], GENERAL_OPTIONS
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert init_integration.options == GENERAL_OPTIONS
        # The entry was reloaded, so the new coordinator carries the options.
        coordinator = init_integration.runtime_data
        assert coordinator.reduction_percent == 40
        assert coordinator.update_interval == timedelta(minutes=10)
        assert coordinator.trend_window_minutes == 90

    async def test_inverted_thresholds_rejected(self, hass, init_integration) -> None:
        result = await _open_options(hass, init_integration, "general")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {**GENERAL_OPTIONS, CONF_BUSY_THRESHOLD: 3.0, CONF_CROWDED_THRESHOLD: 2.0},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_CROWDED_THRESHOLD: "thresholds_inverted"}


class TestClubThresholdOverride:
    async def _pick_club(self, hass, entry, club: str = "morayfield"):
        result = await _open_options(hass, entry, "club_thresholds")
        assert result["step_id"] == "club_thresholds"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_CLUB: club}
        )
        assert result["step_id"] == "club_threshold_values"
        assert result["description_placeholders"] == {"club": "Morayfield"}
        return result

    async def test_override_is_saved_and_used(self, hass, init_integration) -> None:
        result = await self._pick_club(hass, init_integration)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BUSY_THRESHOLD: 1.0, CONF_CROWDED_THRESHOLD: 2.0}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert init_integration.options[CONF_CLUB_THRESHOLDS] == {
            "morayfield": {CONF_BUSY_THRESHOLD: 1.0, CONF_CROWDED_THRESHOLD: 2.0}
        }
        # 20 people on 670 m2 is 1.07 per 36 m2: quiet by default, busy at 1.0.
        assert hass.states.get("sensor.morayfield_busyness").state == "busy"

    async def test_blank_values_clear_the_override(
        self, hass, init_integration
    ) -> None:
        result = await self._pick_club(hass, init_integration)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BUSY_THRESHOLD: 1.0, CONF_CROWDED_THRESHOLD: 2.0}
        )
        await hass.async_block_till_done()

        # The form is prefilled with the existing override.
        result = await self._pick_club(hass, init_integration)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert init_integration.options[CONF_CLUB_THRESHOLDS] == {}
        assert hass.states.get("sensor.morayfield_busyness").state == "quiet"

    @pytest.mark.parametrize(
        ("values", "errors"),
        [
            ({CONF_BUSY_THRESHOLD: 1.0}, {"base": "club_threshold_partial"}),
            ({CONF_CROWDED_THRESHOLD: 2.0}, {"base": "club_threshold_partial"}),
            (
                {CONF_BUSY_THRESHOLD: 2.0, CONF_CROWDED_THRESHOLD: 1.0},
                {CONF_CROWDED_THRESHOLD: "thresholds_inverted"},
            ),
        ],
    )
    async def test_invalid_pairs_rejected(
        self, hass, init_integration, values, errors
    ) -> None:
        result = await self._pick_club(hass, init_integration)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], values
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == errors

    async def test_aborts_before_any_club_is_discovered(
        self, hass, init_integration, portal
    ) -> None:
        portal.clubs = []
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()

        result = await _open_options(hass, init_integration, "club_thresholds")
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_clubs_discovered"
