"""host.py — OXSHost: the single object extensions talk to.

Every extension's ``register(host)`` receives one :class:`OXSHost` instance.
Every method the host exposes is gated by :func:`require_capability` or
:func:`require_privileged`. Calling a method whose capability is not in
the extension's declared ``manifest.capabilities`` (or whose privileged
permission is not in ``manifest.privileged_permissions``) raises
:class:`OXSCapabilityError` and writes an audit entry.

PR 2 ships:
  - the gates framework
  - identity helpers (``get_logger``, ``get_settings``, ``get_data_path``,
    ``get_plugin_path``)
  - lifecycle decorators (delegating to :mod:`lifecycle`)
  - capability recorders that the host keeps in memory for inspection
    (``get_registered_tools``, ``get_registered_routes``, ...)
  - ``OXSCapabilityError`` (re-exported here for type visibility)

PR 3 layers the actual wiring on top — :meth:`OXSHost.add_route` mounts
the route to FastAPI, :meth:`OXSHost.register_mcp_tool` registers with
McpManager, etc. The PR 2 API surface is identical; the body of each
method changes.
"""

from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .audit import audit_log
from .lifecycle import (
    LifecycleHooks,
    on_disable,
    on_enable,
    on_install,
    on_uninstall,
)


# ── Capability gates ───────────────────────────────────────────────────────


class OXSCapabilityError(PermissionError):
    """Raised when an extension calls a host method it has not declared."""

    def __init__(self, extension_id: str, capability: str, method: str = ""):
        self.extension_id = extension_id
        self.capability = capability
        self.method = method
        super().__init__(
            f"extension '{extension_id}' lacks capability '{capability}'"
            + (f" (method={method})" if method else "")
        )


def _gate(capability: str, *, privileged: bool = False):
    """Build a decorator that enforces the capability and audits violations."""
    def decorator(method: Callable) -> Callable:
        method_name = getattr(method, "__name__", repr(method))

        def wrapper(self, *args, **kwargs):
            declared = self._declared_capabilities.get(capability, False)
            granted_priv = self._granted_privileged.get(capability, False)
            if privileged:
                if not granted_priv:
                    self._record_violation(capability, method_name)
                    raise OXSCapabilityError(
                        self.extension_id, capability, method_name,
                    )
            else:
                if not declared:
                    self._record_violation(capability, method_name)
                    raise OXSCapabilityError(
                        self.extension_id, capability, method_name,
                    )
            return method(self, *args, **kwargs)

        wrapper.__name__ = method_name
        wrapper.__wrapped__ = method  # for introspection
        return wrapper

    decorator.__oxs_capability__ = capability
    decorator.__oxs_privileged__ = privileged
    return decorator


def require_capability(capability: str):
    """Decorator: gate a method on a safe/medium capability declaration."""
    return _gate(capability, privileged=False)


def require_privileged(capability: str):
    """Decorator: gate a method on an explicit privileged permission grant."""
    return _gate(capability, privileged=True)


# ── Recorder payloads ─────────────────────────────────────────────────────


@dataclass
class ToolRegistration:
    name: str
    schema: dict
    handler: Callable


@dataclass
class MCPToolRegistration:
    server_name: str
    tool_name: str
    schema: dict
    handler: Callable
    # Optional rich description — semantic text used both in the OpenAI tool
    # list and for the ``tool_index`` RAG pass. When omitted the bridge
    # falls back to ``"[<ext_id>] <tool_name>"`` (preserved for backward
    # compatibility).
    description_rich: str | None = None
    # Declare the tool to the capability/approval gate as a brokered private
    # read. Without this the gate treats an OXS tool as unknown and refuses
    # the call once any untrusted context has been seen (which the tool's own
    # catalog injection guarantees). Results are still treated as untrusted
    # external data either way. Default False keeps unknown tools failing high.
    read_only: bool = False


@dataclass
class RouteRegistration:
    path: str
    handler: Callable
    auth: str
    methods: tuple[str, ...] = ("GET",)


@dataclass
class StaticRegistration:
    url_prefix: str
    directory: str


@dataclass
class EventSubscription:
    event_name: str
    handler: Callable


@dataclass
class WebhookRegistration:
    path_suffix: str
    event_filter: list
    handler: Callable
    auth_token: str | None = None


@dataclass
class SidebarRegistration:
    id: str
    icon: str
    label: str
    panel_id: str | None = None


@dataclass
class PanelRegistration:
    id: str
    title: str
    source_url: str | None = None


@dataclass
class SlashRegistration:
    command: str
    description: str
    handler: Callable


@dataclass
class SettingsTabRegistration:
    tab: str
    title: str
    source_url: str


@dataclass
class TaskActionRegistration:
    name: str
    schema: dict
    handler: Callable


# ── Settings namespace ─────────────────────────────────────────────────────


class SettingsNamespace:
    """Per-extension settings, scoped to ``oxs.extensions.<id>.*``.

    Backed by ``data/oxs/permissions/<id>.json`` plus an in-memory cache
    for the running session. Every ``set()`` is persisted synchronously
    so a container restart does not silently wipe operator-entered
    values like ``api_token`` (whats2api_bridge) — that bug surfaced
    after the auto-restore fix: handlers came back online but every
    token/key the operator had typed in the UI was gone, so calls
    returned ``api_token is not configured`` on every restart.
    """

    def __init__(self, extension_id: str):
        self.extension_id = extension_id
        self._values: dict[str, Any] = {}
        self._load_from_disk()

    @property
    def _path(self) -> str:
        # Lazy import to avoid a hard cycle with core.extensions.__init__
        # pulling in src.constants before it's fully resolved.
        from src.constants import OXS_PERMISSIONS_DIR
        return os.path.join(OXS_PERMISSIONS_DIR, f"{self.extension_id}.json")

    def _load_from_disk(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt file shouldn't break enable(); the in-memory
            # values from on_enable's _ensure_settings seed the
            # namespace again, so the next set() overwrites the bad file.
            import logging
            logging.getLogger(__name__).warning(
                "OXS settings load skipped for %s: %s",
                self.extension_id, exc,
            )
            return
        if isinstance(data, dict):
            self._values.update(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value
        self._persist()

    def _persist(self) -> None:
        try:
            from src.constants import OXS_PERMISSIONS_DIR
            os.makedirs(OXS_PERMISSIONS_DIR, exist_ok=True)
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._values, f, sort_keys=True)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            # Persistence is best-effort; an unwritable volume shouldn't
            # bring down the request. The in-memory value still applies
            # for this process; a future set() will retry the write.
            import logging
            logging.getLogger(__name__).warning(
                "OXS settings persist failed for %s: %s",
                self.extension_id, exc,
            )

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)


# ── OXSHost ────────────────────────────────────────────────────────────────


class OXSHost:
    """The single object extensions talk to. See ``specs/oxs.md`` for full API."""

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        extension_id: str,
        extension_version: str,
        *,
        declared_capabilities: dict[str, bool],
        granted_privileged: dict[str, bool] | None = None,
        data_path: str,
        plugin_path: str,
    ):
        self.extension_id = extension_id
        self.extension_version = extension_version
        # What the extension declared in its manifest.
        self._declared_capabilities: dict[str, bool] = dict(declared_capabilities)
        # What the operator explicitly granted. Privileged permissions only.
        self._granted_privileged: dict[str, bool] = dict(granted_privileged or {})
        # File paths.
        self._data_path = data_path
        self._plugin_path = plugin_path
        # Logger, scoped per-extension.
        self._logger = logging.getLogger(f"oxs.{extension_id}")
        # Lifecycle hooks collected by ``register(host)``.
        self.hooks = LifecycleHooks(extension_id)
        # Per-extension settings namespace.
        self.settings = SettingsNamespace(extension_id)
        # In-memory recorders — what the extension asked to install.
        # PR 3 wires each recorder to the real FastAPI / MCP / event bus.
        self.tools: list[ToolRegistration] = []
        self.mcp_tools: list[MCPToolRegistration] = []
        self.routes: list[RouteRegistration] = []
        self.statics: list[StaticRegistration] = []
        self.event_subs: list[EventSubscription] = []
        self.event_fires_during_enable: list[tuple[str, dict]] = []
        self.webhooks: list[WebhookRegistration] = []
        self.sidebar: list[SidebarRegistration] = []
        self.panels: list[PanelRegistration] = []
        self.slash_commands: list[SlashRegistration] = []
        self.settings_tabs: list[SettingsTabRegistration] = []
        self.task_actions: list[TaskActionRegistration] = []
        # Audit bookkeeping.
        self.violations: list[dict[str, Any]] = []
        # Privileged RPC requests the extension raised during the enable cycle.
        # PR 3 will execute them at host.run_enable time.
        self.privileged_calls: list[tuple[str, tuple, dict]] = []

    # ── Identity ─────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return self.extension_id

    @property
    def version(self) -> str:
        return self.extension_version

    def get_logger(self) -> logging.Logger:
        return self._logger

    def get_settings(self) -> SettingsNamespace:
        return self.settings

    def get_data_path(self, subdir: str = "") -> str:
        """Return the absolute path to the extension's data directory.

        ``subdir`` (optional) is joined to the data root. The caller is
        responsible for ensuring ``subdir`` is allowed by its
        ``filesystem_data_subpath`` permission. ``OXSHost`` does not
        enforce path scoping here — PR 3's storage.py does that.
        """
        if subdir:
            return os.path.join(self._data_path, subdir)
        return self._data_path

    def get_plugin_path(self) -> str:
        return self._plugin_path

    # ── Lifecycle decorators ─────────────────────────────────────────────

    def on_install(self, func: Callable) -> Callable:
        self.hooks.add("on_install", func)
        return func

    def on_enable(self, func: Callable) -> Callable:
        self.hooks.add("on_enable", func)
        return func

    def on_disable(self, func: Callable) -> Callable:
        self.hooks.add("on_disable", func)
        return func

    def on_uninstall(self, func: Callable) -> Callable:
        self.hooks.add("on_uninstall", func)
        return func

    # ── Internal helpers ─────────────────────────────────────────────────

    def _record_violation(self, capability: str, method: str) -> None:
        rec = {"capability": capability, "method": method}
        self.violations.append(rec)
        audit_log(
            "extension.capability.violation",
            extension_id=self.extension_id,
            actor=f"extension:{self.extension_id}",
            payload=rec,
        )

    # ── Capability-recording methods (PR 2 placeholder; PR 3 wires them) ─

    @require_capability("tools")
    def register_tool(self, name: str, schema: dict, handler: Callable) -> None:
        self.tools.append(ToolRegistration(name=name, schema=schema, handler=handler))

    @require_capability("mcp_tools")
    def register_mcp_tool(
        self, server_name: str, tool_name: str, schema: dict, handler: Callable,
        description_rich: str | None = None,
        read_only: bool = False,
    ) -> None:
        self.mcp_tools.append(MCPToolRegistration(
            server_name=server_name, tool_name=tool_name,
            schema=schema, handler=handler,
            description_rich=description_rich,
            read_only=read_only,
        ))

    @require_capability("routes")
    def add_route(
        self, path: str, handler: Callable, auth: str = "user",
        methods: tuple[str, ...] = ("GET",),
    ) -> None:
        if auth not in {"user", "admin", "public"}:
            raise ValueError(f"add_route auth must be user|admin|public, got {auth!r}")
        self.routes.append(RouteRegistration(
            path=path, handler=handler, auth=auth, methods=tuple(methods),
        ))

    @require_privileged("admin_routes")
    def add_admin_route(
        self, path: str, handler: Callable,
        methods: tuple[str, ...] = ("GET",),
    ) -> None:
        self.add_route(path, handler, auth="admin", methods=methods)

    @require_capability("static")
    def add_static(self, url_prefix: str, directory: str) -> None:
        self.statics.append(StaticRegistration(url_prefix=url_prefix, directory=directory))

    @require_capability("events_sub")
    def subscribe_event(self, event_name: str, handler: Callable) -> None:
        self.event_subs.append(EventSubscription(event_name=event_name, handler=handler))

    @require_capability("events_fire")
    def fire_event(
        self, event_name: str, payload: dict | None = None, owner: str | None = None,
    ) -> None:
        # We don't fire during on_enable — we record the request and the
        # registry dispatches it after the host's hooks complete so the
        # extension does not race against its own enable.
        self.event_fires_during_enable.append(
            (event_name, payload or {}, owner),
        )

    @require_capability("webhooks_in")
    def register_webhook(
        self, path_suffix: str, event_filter: list, handler: Callable,
        auth_token: str | None = None,
    ) -> None:
        self.webhooks.append(WebhookRegistration(
            path_suffix=path_suffix, event_filter=list(event_filter),
            handler=handler, auth_token=auth_token,
        ))

    @require_capability("ui_sidebar")
    def add_sidebar_item(
        self, id: str, icon: str, label: str, panel_id: str | None = None,
    ) -> None:
        self.sidebar.append(SidebarRegistration(
            id=id, icon=icon, label=label, panel_id=panel_id,
        ))

    @require_capability("ui_panel")
    def add_panel(
        self, id: str, title: str, source_url: str | None = None,
        html: str | None = None,
    ) -> None:
        self.panels.append(PanelRegistration(id=id, title=title, source_url=source_url))

    @require_capability("ui_slash")
    def register_slash_command(
        self, command: str, description: str, handler: Callable,
    ) -> None:
        self.slash_commands.append(SlashRegistration(
            command=command, description=description, handler=handler,
        ))

    @require_capability("ui_settings")
    def add_settings_tab(
        self, tab: str, title: str, source_url: str,
    ) -> None:
        self.settings_tabs.append(SettingsTabRegistration(
            tab=tab, title=title, source_url=source_url,
        ))

    @require_capability("task_actions")
    def register_task_action(
        self, name: str, schema: dict, handler: Callable,
    ) -> None:
        self.task_actions.append(TaskActionRegistration(
            name=name, schema=schema, handler=handler,
        ))

    @require_capability("settings_read")
    def read_setting(self, key: str, default: Any = None) -> Any:
        """Read a core setting. Allowed keys are scoped by the extension's
        ``permissions.settings_read`` glob. PR 3 enforces the glob; PR 2
        returns ``default`` for any key to avoid surprise reads."""
        return default


# ── Re-exports ─────────────────────────────────────────────────────────────


__all__ = [
    "OXSHost",
    "OXSCapabilityError",
    "require_capability",
    "require_privileged",
    "SettingsNamespace",
    # Recorders — for inspection by the registry and tests.
    "ToolRegistration",
    "MCPToolRegistration",
    "RouteRegistration",
    "StaticRegistration",
    "EventSubscription",
    "WebhookRegistration",
    "SidebarRegistration",
    "PanelRegistration",
    "SlashRegistration",
    "SettingsTabRegistration",
    "TaskActionRegistration",
    # Lifecycle decorator re-exports for convenience.
    "on_install",
    "on_enable",
    "on_disable",
    "on_uninstall",
]