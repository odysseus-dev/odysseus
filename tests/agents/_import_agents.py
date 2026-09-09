"""Load ``services.agents`` without executing the heavy ``services`` package."""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SERVICES = _ROOT / "services"
_AGENTS = _SERVICES / "agents"


def ensure_agents_package() -> None:
    """Register a path-only ``services`` parent so agent modules import cleanly."""
    existing = sys.modules.get("services")
    if existing is not None and getattr(existing, "__file__", None):
        return
    if "services" not in sys.modules:
        parent = types.ModuleType("services")
        parent.__path__ = [str(_SERVICES)]
        parent.__package__ = "services"
        sys.modules["services"] = parent
    if "services.agents" not in sys.modules:
        pkg = types.ModuleType("services.agents")
        pkg.__path__ = [str(_AGENTS)]
        pkg.__package__ = "services.agents"
        pkg.__file__ = str(_AGENTS / "__init__.py")
        sys.modules["services.agents"] = pkg
        sys.modules["services"].agents = pkg
