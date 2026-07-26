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
