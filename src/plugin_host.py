"""Plugin host facade.

Provides a controlled `host` object passed to each plugin's `register(host)`
function.  The facade gates every operation by the plugin's declared
capabilities so a plugin cannot silently exceed its manifest.
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Registries populated by plugins during startup
_routers: dict[str, list[APIRouter]] = {}
_statics: dict[str, list[str]] = {}
_providers: dict[str, Any] = {}
_settings_sections: dict[str, dict[str, Any]] = {}
_tools: dict[str, dict[str, Any]] = {}


class PluginHost:
    """Facade exposed to a plugin via register(host)."""

    def __init__(self, plugin_name: str, capabilities: list[str], app: Any):
        self._name = plugin_name
        self._caps = set(capabilities)
        self._app = app

    def _require(self, cap: str) -> None:
        if cap not in self._caps:
            raise PermissionError(
                f"Plugin '{self._name}' lacks capability '{cap}'. "
                f"Add it to your manifest's 'capabilities' list to use this API."
            )

    def add_router(self, router: APIRouter, *, admin: bool = False) -> None:
        """Register a FastAPI router.

        Args:
            router: The APIRouter to include.
            admin: If True, requires the privileged `manage_plugins` capability.
        """
        self._require("routes")
        if admin:
            self._require("manage_plugins")
        self._app.include_router(router)
        _routers.setdefault(self._name, []).append(router)
        logger.info("[%s] Registered router (admin=%s)", self._name, admin)

    def add_static(self, path: str, directory: str) -> None:
        """Mount a static file directory at the given URL path."""
        self._require("routes")
        self._app.mount(
            path,
            StaticFiles(directory=directory),
            name=f"{self._name}_static",
        )
        _statics.setdefault(self._name, []).append(path)
        logger.info("[%s] Mounted static files at %s", self._name, path)

    def register_provider(self, name: str, provider_class: type) -> None:
        """Register an LLM or search provider.

        Note: this records the intent in an in-memory registry.  The actual
        wiring into the inference path is handled by core once the internal
        provider system exposes a canonical registration API.
        """
        self._require("provider")
        _providers.setdefault(self._name, {})[name] = provider_class
        logger.info("[%s] Registered provider '%s'", self._name, name)

    def add_settings_section(
        self, id: str, label: str, render_fn: Callable[[], str]
    ) -> None:
        """Add a settings tab or section."""
        self._require("settings")
        _settings_sections.setdefault(self._name, {})[id] = {
            "label": label,
            "render": render_fn,
        }
        logger.info("[%s] Added settings section '%s'", self._name, id)

    def add_tool(self, name: str, schema: dict[str, Any], fn: Callable[..., Any]) -> None:
        """Register an agent tool.

        Note: this records the intent in an in-memory registry.  The actual
        wiring into the agent tool dispatcher is handled by core once the
        internal tool system exposes a canonical registration API.
        """
        self._require("tools")
        _tools.setdefault(self._name, {})[name] = {"schema": schema, "fn": fn}
        logger.info("[%s] Registered tool '%s'", self._name, name)

    @property
    def capabilities(self) -> set[str]:
        return set(self._caps)


def get_registered_routers(plugin_name: str) -> list[APIRouter]:
    return list(_routers.get(plugin_name, []))


def get_registered_statics(plugin_name: str) -> list[str]:
    return list(_statics.get(plugin_name, []))


def get_registered_providers(plugin_name: str) -> dict[str, Any]:
    return dict(_providers.get(plugin_name, {}))


def get_registered_settings(plugin_name: str) -> dict[str, Any]:
    return dict(_settings_sections.get(plugin_name, {}))


def get_registered_tools(plugin_name: str) -> dict[str, Any]:
    return dict(_tools.get(plugin_name, {}))


def unregister_all(plugin_name: str) -> None:
    """Remove all registrations for a plugin.  Called on disable/uninstall."""
    for router in _routers.pop(plugin_name, []):
        try:
            # FastAPI doesn't support router removal, but we can track it
            # for a future reload-based approach
            pass
        except Exception:
            pass
    _statics.pop(plugin_name, None)
    _providers.pop(plugin_name, None)
    _settings_sections.pop(plugin_name, None)
    _tools.pop(plugin_name, None)
    logger.info("[%s] Unregistered all contributions", plugin_name)
