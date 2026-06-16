"""Plugin runtime API for backend Python hooks.

Provides a controlled `register(host)` pattern and a per-plugin
`odysseus` module for settings and logging.
"""

import importlib
import importlib.util
import json
import logging
import os
import sys
from typing import Any

from src.constants import DATA_DIR
from src.plugin_host import PluginHost
from src.plugin_schema import PluginValidationError, validate_manifest

logger = logging.getLogger(__name__)

PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")
REPO_PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")

# Scoped plugin storage (in-memory + persisted JSON file)
_plugin_settings: dict[str, dict[str, Any]] = {}


def _plugin_settings_path() -> str:
    return os.path.join(PLUGINS_DIR, "plugin_settings.json")


def _load_settings():
    global _plugin_settings
    try:
        with open(_plugin_settings_path(), "r", encoding="utf-8") as f:
            _plugin_settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _plugin_settings = {}


def _save_settings():
    try:
        with open(_plugin_settings_path(), "w", encoding="utf-8") as f:
            json.dump(_plugin_settings, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save plugin settings: %s", e)


class PluginContext:
    """Runtime context exposed to a backend plugin via the fake 'odysseus' module."""

    def __init__(self, plugin_name: str):
        self.plugin_name = plugin_name

    def get_setting(self, key: str) -> Any | None:
        _load_settings()
        return _plugin_settings.get(self.plugin_name, {}).get(key)

    def set_setting(self, key: str, value: Any):
        _load_settings()
        if self.plugin_name not in _plugin_settings:
            _plugin_settings[self.plugin_name] = {}
        _plugin_settings[self.plugin_name][key] = value
        _save_settings()

    def log(self, level: str, message: str):
        lvl = getattr(logging, level.upper(), logging.INFO)
        logger.log(lvl, "[%s] %s", self.plugin_name, message)

    def manifest(self) -> dict:
        """Return the plugin's manifest as a dict."""
        # Check both local and pip-installed paths
        candidates = [
            os.path.join(PLUGINS_DIR, self.plugin_name, "odysseus-plugin.json"),
        ]
        try:
            import importlib.metadata as md
            for ep in md.entry_points(group="odysseus.plugins"):
                if ep.name == self.plugin_name:
                    import inspect
                    mod = ep.load()
                    candidates.append(
                        os.path.join(os.path.dirname(inspect.getfile(mod)), "odysseus-plugin.json")
                    )
        except Exception:
            pass
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
        return {}


def _make_module(plugin_name: str) -> Any:
    """Return a fake 'odysseus' module for the given plugin name."""
    ctx = PluginContext(plugin_name)
    mod = type(sys)("odysseus")
    mod.get_setting = ctx.get_setting
    mod.set_setting = ctx.set_setting
    mod.log = ctx.log
    mod.manifest = ctx.manifest
    return mod


def _load_manifest(plugin_name: str) -> dict[str, Any] | None:
    """Find and validate a plugin manifest by name."""
    # Local (DATA_DIR)
    local_path = os.path.join(PLUGINS_DIR, plugin_name, "odysseus-plugin.json")
    if os.path.isfile(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            validate_manifest(manifest)
            return manifest
        except PluginValidationError:
            pass
    # Bundled repo plugins
    repo_path = os.path.join(REPO_PLUGINS_DIR, plugin_name, "odysseus-plugin.json")
    if os.path.isfile(repo_path):
        try:
            with open(repo_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            validate_manifest(manifest)
            return manifest
        except PluginValidationError:
            pass
    # Entry-point
    try:
        import importlib.metadata as md
        for ep in md.entry_points(group="odysseus.plugins"):
            if ep.name == plugin_name:
                import inspect
                module_dir = os.path.dirname(inspect.getfile(ep.load()))
                with open(os.path.join(module_dir, "odysseus-plugin.json"), "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                validate_manifest(manifest)
                return manifest
    except Exception:
        pass
    return None


def _resolve_entry_point(entry_point: str):
    """Import and return the callable described by 'module.path:callable_name'."""
    if ":" in entry_point:
        module_path, callable_name = entry_point.split(":", 1)
    else:
        parts = entry_point.rsplit(".", 1)
        module_path = parts[0]
        callable_name = parts[1]
    module = importlib.import_module(module_path)
    return getattr(module, callable_name)


def call_register(plugin_name: str, app: Any) -> bool:
    """Call a plugin's register(host) function with a PluginHost facade.

    Returns True if registration succeeded, False otherwise.
    """
    manifest = _load_manifest(plugin_name)
    if not manifest:
        logger.warning("Could not load manifest for plugin %s", plugin_name)
        return False

    entry_point = manifest.get("entry_point", "")
    if not entry_point:
        logger.warning("Plugin %s has no entry_point", plugin_name)
        return False

    capabilities = manifest.get("capabilities", [])
    host = PluginHost(plugin_name, capabilities, app)

    # Determine plugin root for sys.path injection
    plugin_root = os.path.join(PLUGINS_DIR, plugin_name)
    if not os.path.isdir(plugin_root):
        plugin_root = os.path.join(REPO_PLUGINS_DIR, plugin_name)
    _path_inserted = False
    if os.path.isdir(plugin_root) and plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)
        _path_inserted = True

    # Inject a scoped fake odysseus module so the plugin can import it.
    # Save and restore the previous entry so plugins don't clobber each other.
    _previous_odysseus = sys.modules.get("odysseus")
    sys.modules["odysseus"] = _make_module(plugin_name)

    try:
        register_fn = _resolve_entry_point(entry_point)
        register_fn(host)
        logger.info("Plugin %s registered successfully", plugin_name)
        return True
    except PermissionError as e:
        logger.warning("Plugin %s capability violation: %s", plugin_name, e)
        return False
    except Exception as e:
        logger.warning("Plugin %s registration failed: %s", plugin_name, e)
        return False
    finally:
        if _previous_odysseus is None:
            sys.modules.pop("odysseus", None)
        else:
            sys.modules["odysseus"] = _previous_odysseus
        if _path_inserted:
            try:
                sys.path.remove(plugin_root)
            except ValueError:
                pass


def _load_enabled() -> dict[str, bool]:
    enabled_path = os.path.join(PLUGINS_DIR, "enabled.json")
    try:
        with open(enabled_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _scan_and_register(plugins_dir: str, enabled: dict[str, bool], app: Any):
    if not os.path.isdir(plugins_dir):
        return
    for entry in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, entry)
        if not os.path.isdir(plugin_dir):
            continue
        manifest_path = os.path.join(plugin_dir, "odysseus-plugin.json")
        if not os.path.isfile(manifest_path):
            continue
        if not enabled.get(entry, False):
            continue
        call_register(entry, app)


def startup_all(app: Any):
    """Call register(host) for every installed/enabled plugin that has an entry_point."""
    enabled = _load_enabled()
    # Local plugins (DATA_DIR)
    _scan_and_register(PLUGINS_DIR, enabled, app)
    # Bundled repo plugins
    _scan_and_register(REPO_PLUGINS_DIR, enabled, app)
    # Entry-point plugins
    try:
        import importlib.metadata as md
        for ep in md.entry_points(group="odysseus.plugins"):
            if not enabled.get(ep.name, False):
                continue
            call_register(ep.name, app)
    except Exception:
        pass


def shutdown_all():
    """Graceful shutdown hook — currently a no-op since plugins run in-process."""
    pass
