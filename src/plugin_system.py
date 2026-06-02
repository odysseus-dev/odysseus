"""Odysseus plugin system — a Blender-style, drop-in plugin architecture.

Goal: adding a feature to Odysseus should be as easy as dropping a folder in
``plugins/`` — no editing core. A plugin can register agent tools, mount HTTP
routes, run background services, and add UI, and can be enabled/disabled at
runtime without a restart.

A plugin is a folder ``plugins/<id>/plugin.py`` (or a single-file
``plugins/<id>_plugin.py``) exposing:

    PLUGIN = {                       # manifest — like Blender's bl_info
        "name": "Human Name",
        "version": "1.0.0",
        "author": "you",
        "description": "What it does.",
        "category": "Networking",     # optional grouping for the UI
        "requires": [],               # optional: pip pkgs / external bins (informational)
        "permission": "admin",        # who may toggle/use it: "admin" (default) or "user"
    }

    def setup(ctx):    ...            # like register(): wire routes/services/tools
    def teardown(ctx): ...            # like unregister(): undo setup (optional)

``ctx`` is a :class:`PluginContext`. Use its helpers (``add_router``,
``add_service``, ``register_tool``) rather than touching ``ctx.app`` directly so
the manager can TRACK what you registered and tear it down cleanly on disable.

Design notes for contributors
-----------------------------
* **Isolation** — a plugin that raises during import or ``setup`` is recorded
  with its traceback and skipped; it never crashes app startup.
* **State** — which plugins are enabled persists in ``<data>/plugins.json``.
  A newly-discovered plugin defaults to enabled (drop-in just works); an admin
  can disable it from the Plugins panel.
* **Live toggle** — ``add_router``/``add_service``/``register_tool`` are
  reversible, so enable/disable take effect immediately (best-effort for routes;
  the OpenAPI schema is rebuilt on next access).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# A safe plugin id: used as a filesystem path component AND a Python import name.
_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")


def plugins_dir() -> str:
    return os.environ.get(
        "ODYSSEUS_PLUGINS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"),
    )


def _data_root() -> str:
    """Writable data root: ODYSSEUS_DATA_DIR if set, else the app's DATA_DIR."""
    root = os.environ.get("ODYSSEUS_DATA_DIR")
    if root:
        return root
    try:
        from core.constants import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.path.join(os.path.dirname(plugins_dir()), "data")


def _state_path() -> str:
    return os.path.join(_data_root(), "plugins.json")


# ---------------------------------------------------------------------------
# PluginContext — the controlled surface a plugin's setup(ctx) receives
# ---------------------------------------------------------------------------
@dataclass
class PluginContext:
    """Handed to ``setup(ctx)`` / ``teardown(ctx)``. Prefer its helpers over
    poking ``app`` directly so the manager can undo everything on disable."""
    plugin_id: str
    app: Any
    data_dir: str
    logger: logging.Logger
    # internal teardown tracking
    _routes: List[Any] = field(default_factory=list)
    _services: List["tuple[Optional[Callable], Optional[Callable]]"] = field(default_factory=list)
    _tools: List[str] = field(default_factory=list)
    _cleanups: List[Callable[[], None]] = field(default_factory=list)

    # -- routes -------------------------------------------------------------
    def add_router(self, router, **include_kwargs) -> None:
        """Mount a FastAPI APIRouter; its routes are tracked for clean removal.

        Routes MUST live under ``/api/plugins/`` — otherwise a plugin could mount
        under an auth-exempt prefix (``/static``, ``/api/auth``, ``/api/health``)
        and expose an UNAUTHENTICATED endpoint, since the auth middleware gates by
        path. Off-namespace routes are rolled back and rejected."""
        before = len(self.app.router.routes)
        self.app.include_router(router, **include_kwargs)
        added = self.app.router.routes[before:]
        bad = [r for r in added if not str(getattr(r, "path", "")).startswith("/api/plugins/")]
        if bad:
            for r in added:
                try:
                    self.app.router.routes.remove(r)
                except ValueError:
                    pass
            raise ValueError(
                "plugin routes must be mounted under /api/plugins/<id>/ "
                f"(rejected: {[getattr(r, 'path', '?') for r in bad][:3]})")
        self._routes.extend(added)

    # -- background services -----------------------------------------------
    def add_service(self, start: Optional[Callable] = None,
                    stop: Optional[Callable] = None) -> None:
        """Register a background service. ``start`` runs now; ``stop`` runs on
        teardown/disable. Both are plain callables (sync)."""
        self._services.append((start, stop))
        if start:
            start()

    # -- agent tools (optional; needs the tool registry) -------------------
    def register_tool(self, spec) -> None:
        """Register an agent tool via the ToolSpec registry, if present."""
        try:
            from src.tool_registry import register_tool as _rt
        except Exception as e:  # tool registry not installed in this build
            self.logger.warning("register_tool unavailable (%s); skipping %r", e, getattr(spec, "name", spec))
            return
        _rt(spec)
        self._tools.append(getattr(spec, "name", str(spec)))

    def on_teardown(self, fn: Callable[[], None]) -> None:
        """Register an arbitrary cleanup callable run on teardown."""
        self._cleanups.append(fn)


@dataclass
class PluginRecord:
    plugin_id: str
    path: str
    manifest: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    status: str = "discovered"          # discovered | loaded | disabled | error
    error: Optional[str] = None
    module: Any = None
    ctx: Optional[PluginContext] = None

    def public(self) -> Dict[str, Any]:
        m = self.manifest or {}
        return {
            "id": self.plugin_id,
            "name": m.get("name", self.plugin_id),
            "version": m.get("version", ""),
            "author": m.get("author", ""),
            "description": m.get("description", ""),
            "category": m.get("category", "General"),
            "permission": m.get("permission", "admin"),
            "requires": m.get("requires", []),
            # Optional UI contribution: {"open": "/api/.../page", "label": "Open"}.
            # Sanitized (see _safe_ui) so `open` must be a same-origin path — the
            # Plugins panel renders it as a link, so block javascript:/`//evil`.
            "ui": _safe_ui(m),
            "enabled": self.enabled,
            "status": self.status,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------
class PluginManager:
    def __init__(self, app: Any = None, directory: Optional[str] = None):
        self.app = app
        self.directory = directory or plugins_dir()
        self.records: Dict[str, PluginRecord] = {}
        self._lock = threading.RLock()

    # -- persisted enable/disable state ------------------------------------
    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(_state_path(), "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_state(self) -> None:
        path = _state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state = {pid: {"enabled": r.enabled} for pid, r in self.records.items()}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, path)   # atomic — a crash mid-write can't truncate the file
        except Exception as e:
            logger.warning("Could not persist plugin state: %s", e)

    # -- discovery ----------------------------------------------------------
    def discover(self) -> None:
        """Scan the plugins dir, read manifests, apply persisted enable state.
        Does not import/run plugin code — that's `setup`/`load`."""
        with self._lock:
            state = self._load_state()
            found: Dict[str, PluginRecord] = {}
            if not os.path.isdir(self.directory):
                self.records = found
                return
            for entry in sorted(os.listdir(self.directory)):
                full = os.path.join(self.directory, entry)
                if os.path.islink(full):
                    continue   # don't load a symlinked entry (could point outside the dir)
                if os.path.isdir(full) and os.path.isfile(os.path.join(full, "plugin.py")):
                    pid, path = entry, os.path.join(full, "plugin.py")
                elif entry.endswith("_plugin.py"):
                    pid, path = entry[:-len("_plugin.py")], full
                else:
                    continue
                # id is used as a filesystem path + an import name, and the entry
                # file must not be a symlink to code outside the plugins dir.
                if not _ID_RE.match(pid) or os.path.islink(path):
                    continue
                rec = self.records.get(pid) or PluginRecord(plugin_id=pid, path=path)
                rec.path = path
                rec.manifest = _read_manifest(path) or rec.manifest
                rec.enabled = state.get(pid, {}).get("enabled", True)
                found[pid] = rec
            # Tear down any previously-loaded plugin whose folder vanished from
            # disk, so its routes/services don't linger (orphaned otherwise).
            for pid, rec in self.records.items():
                if pid not in found and rec.ctx is not None:
                    self._teardown(rec)
            self.records = found

    # -- load / unload ------------------------------------------------------
    def load_enabled(self, app: Any = None) -> int:
        """Discover, then `setup` every enabled plugin. Returns count loaded."""
        if app is not None:
            self.app = app
        self.discover()
        loaded = 0
        for rec in self.records.values():
            if rec.enabled and self._setup(rec):
                loaded += 1
        if self.records:
            logger.info("Plugin system: %d/%d plugin(s) active", loaded, len(self.records))
        return loaded

    def _import(self, rec: PluginRecord) -> bool:
        try:
            mod_name = "odysseus_plugin_" + rec.plugin_id
            spec = importlib.util.spec_from_file_location(mod_name, rec.path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            rec.module = module
            if not rec.manifest:
                rec.manifest = getattr(module, "PLUGIN", {}) or {}
            return True
        except Exception:
            rec.status, rec.error = "error", traceback.format_exc(limit=6)
            logger.error("Plugin %s failed to import:\n%s", rec.plugin_id, rec.error)
            return False

    def _setup(self, rec: PluginRecord) -> bool:
        with self._lock:
            if rec.status == "loaded":
                return True
            if rec.module is None and not self._import(rec):
                return False
            ctx = PluginContext(
                plugin_id=rec.plugin_id,
                app=self.app,
                data_dir=self._data_dir(rec.plugin_id),
                logger=logging.getLogger("plugin." + rec.plugin_id),
            )
            try:
                setup = getattr(rec.module, "setup", None)
                if callable(setup):
                    setup(ctx)
                rec.ctx, rec.status, rec.error = ctx, "loaded", None
                logger.info("Plugin loaded: %s", rec.plugin_id)
                return True
            except Exception:
                rec.status, rec.error = "error", traceback.format_exc(limit=6)
                logger.error("Plugin %s setup() failed:\n%s", rec.plugin_id, rec.error)
                self._teardown(rec)  # roll back partial registration
                return False

    def _teardown(self, rec: PluginRecord) -> None:
        ctx = rec.ctx
        if not ctx:
            return
        # services
        for _start, stop in reversed(ctx._services):
            if stop:
                try:
                    stop()
                except Exception as e:
                    logger.warning("Plugin %s service stop failed: %s", rec.plugin_id, e)
        # custom cleanups
        for fn in reversed(ctx._cleanups):
            try:
                fn()
            except Exception as e:
                logger.warning("Plugin %s cleanup failed: %s", rec.plugin_id, e)
        # routes
        for route in ctx._routes:
            try:
                self.app.router.routes.remove(route)
            except ValueError:
                pass
        # tools
        if ctx._tools:
            try:
                from src.tool_registry import unregister_tool as _ut
                for name in ctx._tools:
                    _ut(name)
            except Exception:
                pass
        # plugin-level teardown hook
        try:
            td = getattr(rec.module, "teardown", None)
            if callable(td):
                td(ctx)
        except Exception as e:
            logger.warning("Plugin %s teardown() failed: %s", rec.plugin_id, e)
        rec.ctx = None
        try:
            self.app.openapi_schema = None  # force schema rebuild after route change
        except Exception:
            pass

    # -- public toggle API --------------------------------------------------
    def enable(self, plugin_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.records.get(plugin_id)
            if not rec:
                raise KeyError(plugin_id)
            rec.enabled = True
            self._save_state()
            self._setup(rec)
            return rec.public()

    def disable(self, plugin_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.records.get(plugin_id)
            if not rec:
                raise KeyError(plugin_id)
            self._teardown(rec)
            rec.enabled = False
            rec.status = "disabled"
            self._save_state()
            return rec.public()

    def reload(self, plugin_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.records.get(plugin_id)
            if not rec:
                raise KeyError(plugin_id)
            self._teardown(rec)
            rec.module = None
            rec.status = "discovered"   # force _setup to re-import the (possibly new) code
            rec.manifest = _read_manifest(rec.path) or {}
            if rec.enabled:
                self._setup(rec)
            return rec.public()

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.public() for r in sorted(self.records.values(), key=lambda r: r.plugin_id)]

    def shutdown_all(self) -> None:
        """Tear down every loaded plugin (stop services, remove routes). Called
        on app shutdown so background services (tunnels, etc.) don't linger."""
        with self._lock:
            for rec in self.records.values():
                if rec.ctx:
                    self._teardown(rec)

    def _data_dir(self, plugin_id: str) -> str:
        d = os.path.join(_data_root(), "plugins", plugin_id)
        os.makedirs(d, exist_ok=True)
        return d


def _safe_ui(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sanitize the manifest ``ui`` entry for the Plugins panel. ``open`` must be
    a same-origin path (a single leading ``/``) so the rendered Open button can't
    become a ``javascript:`` or protocol-relative (``//evil``) link. Returns None
    if absent or unsafe."""
    ui = manifest.get("ui")
    if not isinstance(ui, dict):
        return None
    open_ = ui.get("open")
    if not (isinstance(open_, str) and open_.startswith("/") and not open_.startswith("//")):
        return None
    label = ui.get("label")
    return {"open": open_, "label": label if isinstance(label, str) and label else "Open"}


def _read_manifest(path: str) -> Dict[str, Any]:
    """Extract the PLUGIN dict WITHOUT executing the module — parse the AST so
    discovery is cheap and a broken plugin body can't break the listing."""
    import ast
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except Exception:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "PLUGIN":
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return {}
    return {}


# Module-level singleton + entry point used by app.py.
MANAGER: Optional[PluginManager] = None


def load_plugins(app: Any) -> PluginManager:
    """Build the manager (if needed) and load all enabled plugins. Idempotent."""
    global MANAGER
    if MANAGER is None:
        MANAGER = PluginManager(app=app)
    else:
        MANAGER.app = app
    MANAGER.load_enabled(app)
    return MANAGER


def get_manager() -> Optional[PluginManager]:
    return MANAGER
