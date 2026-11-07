"""Import the integration's Home-Assistant-free modules for plain pytest.

estimator, hours, density and club_data deliberately avoid Home Assistant
imports so they can be tested without it installed. Importing them normally
would execute custom_components/pfau_occupancy/__init__.py, which does import
Home Assistant, so instead they're loaded under a stub package that has the
component directory on its __path__ — enough for their relative imports
(`from .hours import ...`) to resolve, without the real __init__ running.

Requires PyYAML (a Home Assistant core dependency); see requirements.txt.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType

import pytest

_STUB_PACKAGE = "pfau_occupancy_pure"
_COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "pfau_occupancy"
)


def _ensure_stub_package() -> None:
    if _STUB_PACKAGE in sys.modules:
        return
    package = types.ModuleType(_STUB_PACKAGE)
    package.__path__ = [str(_COMPONENT_DIR)]
    sys.modules[_STUB_PACKAGE] = package


def load_module(name: str) -> ModuleType:
    """Load one module from the integration by name, e.g. load_module("hours")."""
    _ensure_stub_package()
    qualified = f"{_STUB_PACKAGE}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(
        qualified, _COMPONENT_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec because dataclass/StrEnum resolve their module at
    # class-creation time.
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Home Assistant fixtures.
#
# Only defined where pytest-homeassistant-custom-component is installed (its
# harness needs Linux or macOS); everywhere else the Home-Assistant-free tests
# above still run, and test_ha_*.py skip themselves.
# ---------------------------------------------------------------------------
try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:
    pass
else:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.pfau_occupancy import coordinator as _coordinator
    from custom_components.pfau_occupancy.const import DOMAIN
    from ha_support import (
        EMAIL,
        MONDAY_MORNING,
        PASSWORD,
        Portal,
        RemoteClubData,
    )

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):  # type: ignore[no-untyped-def]
        """Let Home Assistant load custom_components/ in the tests."""
        yield

    @pytest.fixture(autouse=True)
    def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry a failed fetch straight away rather than sleeping."""
        monkeypatch.setattr(_coordinator, "FETCH_RETRY_DELAYS", (0, 0))

    @pytest.fixture
    def portal(aioclient_mock):  # type: ignore[no-untyped-def]
        return Portal(aioclient_mock)

    @pytest.fixture
    def remote(aioclient_mock):  # type: ignore[no-untyped-def]
        return RemoteClubData(aioclient_mock)

    @pytest.fixture
    def mock_entry(hass, tmp_path) -> MockConfigEntry:  # type: ignore[no-untyped-def]
        # The harness shares one config directory between tests, and the
        # integration caches clubs.yaml in it, so give each test its own.
        hass.config.config_dir = str(tmp_path)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=EMAIL,
            unique_id=EMAIL.lower(),
            data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        )
        entry.add_to_hass(hass)
        return entry

    @pytest.fixture
    async def init_integration(hass, mock_entry, portal, remote, freezer):  # type: ignore[no-untyped-def]
        """A loaded config entry against the fake portal, at a known time."""
        freezer.move_to(MONDAY_MORNING)
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        yield mock_entry
        if mock_entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
