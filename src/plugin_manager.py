"""Plugin manager — discover, install, and enable/disable plugins.

Plugins are discovered via:
1. Python entry-points (pip-installed, ``odysseus.plugins`` group)
2. Local ``DATA_DIR/plugins/`` directory (dev overrides)
3. Bundled repo ``plugins/`` directory (reference plugins shipped with core)

Remote discover/install are intentionally left out of this PR;
they will be revisited separately once the contract is settled.

Local plugins take precedence over pip-installed ones.
"""
import json
import logging
import os
import shutil
from typing import Any

from src.constants import DATA_DIR
from src.plugin_schema import PluginValidationError, validate_manifest

logger = logging.getLogger(__name__)

PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")
REPO_PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")


def _registry_file() -> str:
    return os.path.join(PLUGINS_DIR, "registry.json")


def _enabled_file() -> str:
    return os.path.join(PLUGINS_DIR, "enabled.json")


def _ensure_dirs():
    os.makedirs(PLUGINS_DIR, exist_ok=True)


def _load_registry() -> dict:
    _ensure_dirs()
    try:
        with open(_registry_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_registry(data: dict):
    _ensure_dirs()
    registry_file = _registry_file()
    tmp = f"{registry_file}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, registry_file)


def _load_enabled() -> dict[str, bool]:
    _ensure_dirs()
    try:
        with open(_enabled_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_enabled(data: dict[str, bool]):
    _ensure_dirs()
    path = _enabled_file()
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_manifest_from_dir(plugin_dir: str) -> dict[str, Any] | None:
    manifest_path = os.path.join(plugin_dir, "odysseus-plugin.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        validate_manifest(manifest)
        return manifest
    except PluginValidationError as e:
        logger.warning("Invalid manifest in %s: %s", plugin_dir, e)
        return None
    except Exception:
        return None


def _load_manifest_from_module(module: Any) -> dict[str, Any] | None:
    import inspect
    module_file = inspect.getfile(module)
    module_dir = os.path.dirname(module_file)
    manifest_path = os.path.join(module_dir, "odysseus-plugin.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        validate_manifest(manifest)
        return manifest
    except PluginValidationError as e:
        logger.warning("Invalid manifest in %s: %s", module_file, e)
        return None
    except Exception:
        return None


class PluginManager:
    def __init__(self):
        self._pip_plugins: dict[str, Any] = {}
        self._local_plugins: dict[str, Any] = {}
        self._refresh()

    def _refresh(self):
        self._pip_plugins.clear()
        self._local_plugins.clear()
        try:
            import importlib.metadata as md
            for ep in md.entry_points(group="odysseus.plugins"):
                try:
                    module = ep.load()
                    manifest = _load_manifest_from_module(module)
                    if manifest:
                        self._pip_plugins[manifest["name"]] = {
                            "manifest": manifest,
                            "module": module,
                            "entry_point": ep,
                            "source": "pip",
                        }
                except Exception as e:
                    logger.warning("Failed to load entry-point %s: %s", ep.name, e)
        except Exception:
            pass
        for root_dir in (PLUGINS_DIR, REPO_PLUGINS_DIR):
            if not os.path.isdir(root_dir):
                continue
            for entry in os.listdir(root_dir):
                plugin_dir = os.path.join(root_dir, entry)
                if not os.path.isdir(plugin_dir):
                    continue
                manifest = _load_manifest_from_dir(plugin_dir)
                if manifest:
                    self._local_plugins[manifest["name"]] = {
                        "manifest": manifest,
                        "dir": plugin_dir,
                        "source": "local",
                    }

    def all_plugins(self) -> dict[str, dict[str, Any]]:
        merged = dict(self._pip_plugins)
        merged.update(self._local_plugins)
        return merged

    def list_installed(self) -> list[dict]:
        plugins = self.all_plugins()
        enabled = _load_enabled()
        results = []
        for name, info in plugins.items():
            manifest = dict(info["manifest"])
            manifest["_source"] = info["source"]
            manifest["_enabled"] = enabled.get(name, False)
            if info["source"] == "local":
                registry = _load_registry()
                reg = registry.get(name, {})
                manifest["_installed_at"] = reg.get("installed_at")
            results.append(manifest)
        return results

    def is_enabled(self, plugin_name: str) -> bool:
        return _load_enabled().get(plugin_name, False)

    def set_enabled(self, plugin_name: str, enabled: bool) -> bool:
        data = _load_enabled()
        data[plugin_name] = enabled
        _save_enabled(data)
        return True

    def uninstall(self, plugin_name: str) -> bool:
        dest = os.path.join(PLUGINS_DIR, plugin_name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        registry = _load_registry()
        if plugin_name in registry:
            del registry[plugin_name]
            _save_registry(registry)
        enabled = _load_enabled()
        if plugin_name in enabled:
            del enabled[plugin_name]
            _save_enabled(enabled)
        return True

    def serve_path(self, plugin_name: str, file_path: str) -> str | None:
        if "/" in plugin_name or "\\" in plugin_name or ".." in plugin_name:
            return None
        for base in (os.path.join(PLUGINS_DIR, plugin_name), os.path.join(REPO_PLUGINS_DIR, plugin_name)):
            target = os.path.normpath(os.path.join(base, file_path))
            if not target.startswith(os.path.normpath(base)):
                continue
            if os.path.exists(target) and os.path.isfile(target):
                return target
        return None

    def get_local_dir(self, plugin_name: str) -> str | None:
        for path in (os.path.join(PLUGINS_DIR, plugin_name), os.path.join(REPO_PLUGINS_DIR, plugin_name)):
            if os.path.isdir(path):
                return path
        return None
