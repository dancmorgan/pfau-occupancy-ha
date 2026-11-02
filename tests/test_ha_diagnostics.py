"""Diagnostics must help debugging without leaking the member's credentials."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from custom_components.pfau_occupancy.diagnostics import (
    async_get_config_entry_diagnostics,
)
from ha_support import EMAIL, PASSWORD


async def test_credentials_are_redacted(hass, init_integration) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    dumped = json.dumps(diagnostics, default=str)
    assert EMAIL not in dumped
    assert EMAIL.lower() not in dumped
    assert PASSWORD not in dumped
    assert diagnostics["entry"]["data"]["email"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["title"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"


async def test_includes_what_is_needed_to_debug(hass, init_integration) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["last_update_success"] is True
    assert diagnostics["clubs"]["morayfield"] == {
        "name": "Morayfield",
        "address": "1 Morayfield Street",
        "occupancy": 40,
        "limit": None,
        "studio_id": 1000000,
        "timezone": "Australia/Brisbane",
    }
    assert diagnostics["estimates"]["morayfield"] == {
        "estimated_occupancy": 20,
        "raw_count": 40,
    }
    assert diagnostics["profiles_loaded"] == ["badzone", "eastside", "morayfield"]
