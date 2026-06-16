"""Plugin manager — discover, install, and enable/disable plugins.

Plugins are discovered via:
1. Python entry-points (pip-installed, ``odysseus.plugins`` group)
2. Local ``DATA_DIR/plugins/`` directory (dev overrides)

Local plugins take precedence over pip-installed ones.
"""
import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from src.constants import DATA_DIR
from src.plugin_schema import PluginValidationError, validate_manifest

logger = logging.getLogger(__name__)

PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")


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


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def _log_install(url: str, plugin_name: str, action: str = "install"):
    _ensure_dirs()
    log_path = os.path.join(PLUGINS_DIR, "install.log")
    line = f"{datetime.utcnow().isoformat()}Z  {action:10}  {plugin_name:40}  {url}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def _to_raw_url(repo_url: str, rel_path: str) -> str | None:
    parsed = urlparse(repo_url.rstrip("/"))
    host = parsed.hostname or ""
    path = parsed.path.strip("/").split("/")
    if len(path) < 2:
        return None
    owner, repo = path[0], path[1]
    repo = repo.removesuffix(".git")
    rest = "/".join(path[2:])
    if "github.com" in host:
        branch = "main"
        if rest.startswith("tree/") or rest.startswith("blob/"):
            parts = rest.split("/", 2)
            if len(parts) >= 2:
                branch = parts[1]
                rest = parts[2] if len(parts) > 2 else ""
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rel_path}"
    if "gitlab.com" in host:
        return f"https://gitlab.com/{owner}/{repo}/-/raw/main/{rel_path}"
    return None


def _fetch_json(url: str, timeout: float = 10.0) -> Any | None:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


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
        if os.path.isdir(PLUGINS_DIR):
            for entry in os.listdir(PLUGINS_DIR):
                plugin_dir = os.path.join(PLUGINS_DIR, entry)
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
            manifest["_enabled"] = enabled.get(name, True)
            if info["source"] == "local":
                registry = _load_registry()
                reg = registry.get(name, {})
                manifest["_installed_at"] = reg.get("installed_at")
            results.append(manifest)
        return results

    def is_enabled(self, plugin_name: str) -> bool:
        return _load_enabled().get(plugin_name, True)

    def set_enabled(self, plugin_name: str, enabled: bool) -> bool:
        data = _load_enabled()
        data[plugin_name] = enabled
        _save_enabled(data)
        return True

    def discover(self, repo_url: str) -> list[dict]:
        root_url = _to_raw_url(repo_url, "plugins.json")
        if not root_url:
            return []
        root = _fetch_json(root_url)
        if not root or not isinstance(root, dict):
            return []
        plugin_paths = root.get("plugins", [])
        if not isinstance(plugin_paths, list):
            return []
        results = []
        for p in plugin_paths:
            manifest_url = _to_raw_url(repo_url, f"{p}/odysseus-plugin.json")
            if not manifest_url:
                continue
            manifest = _fetch_json(manifest_url)
            if not manifest or not isinstance(manifest, dict):
                continue
            try:
                validate_manifest(manifest)
                manifest["_repo_url"] = repo_url
                manifest["_path"] = p
                results.append(manifest)
            except PluginValidationError:
                pass
        return results

    def install(self, repo_url: str, plugin_names: list[str]) -> dict:
        _ensure_dirs()
        parsed = urlparse(repo_url.rstrip("/"))
        host = parsed.hostname or ""
        path = parsed.path.strip("/").split("/")
        if len(path) < 2:
            return {"installed": [], "failed": ["Invalid repo URL"], "needs_restart": False}
        owner, repo = path[0], path[1].removesuffix(".git")
        zip_urls = []
        if "github.com" in host:
            zip_urls = [
                f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip",
                f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip",
            ]
        elif "gitlab.com" in host:
            zip_urls = [
                f"https://gitlab.com/{owner}/{repo}/-/archive/main/{repo}-main.zip",
                f"https://gitlab.com/{owner}/{repo}/-/archive/master/{repo}-master.zip",
            ]
        else:
            return {"installed": [], "failed": ["Unsupported git host"], "needs_restart": False}

        installed = []
        failed = []
        r = None
        last_err = None
        for zip_url in zip_urls:
            try:
                r = httpx.get(zip_url, timeout=30.0, follow_redirects=True)
                r.raise_for_status()
                break
            except Exception as e:
                last_err = e
        if r is None:
            return {"installed": [], "failed": [str(last_err)], "needs_restart": False}

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "repo.zip")
            with open(zip_path, "wb") as f:
                f.write(r.content)
            extract_dir = os.path.join(tmpdir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(extract_dir)
            entries = [e for e in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, e))]
            if not entries:
                return {"installed": [], "failed": ["Empty ZIP"], "needs_restart": False}
            repo_root = os.path.join(extract_dir, entries[0])
            root_manifest_path = os.path.join(repo_root, "plugins.json")
            name_to_path: dict[str, str] = {}
            if os.path.isfile(root_manifest_path):
                try:
                    with open(root_manifest_path, "r", encoding="utf-8") as f:
                        root_manifest = json.load(f)
                    for p in root_manifest.get("plugins", []):
                        manifest = _load_manifest_from_dir(os.path.join(repo_root, p))
                        if manifest:
                            name_to_path[manifest["name"]] = p
                except Exception:
                    pass
            for plugin_name in plugin_names:
                rel_path = name_to_path.get(plugin_name)
                if not rel_path:
                    failed.append(f"{plugin_name}: not found in repo manifest")
                    continue
                src = os.path.join(repo_root, rel_path)
                if not os.path.isdir(src):
                    failed.append(f"{plugin_name}: folder not in archive")
                    continue
                manifest = _load_manifest_from_dir(src)
                if not manifest:
                    failed.append(f"{plugin_name}: invalid manifest")
                    continue
                dest = os.path.join(PLUGINS_DIR, plugin_name)
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                registry = _load_registry()
                registry[plugin_name] = {
                    "installed_at": datetime.utcnow().isoformat() + "Z",
                    "repo_url": repo_url,
                    "version": manifest.get("version", "0.0.0"),
                }
                _save_registry(registry)
                _log_install(repo_url, plugin_name, "install")
                installed.append(plugin_name)
        return {"installed": installed, "failed": failed, "needs_restart": True}

    def uninstall(self, plugin_name: str) -> bool:
        dest = os.path.join(PLUGINS_DIR, plugin_name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        registry = _load_registry()
        if plugin_name in registry:
            repo_url = registry[plugin_name].get("repo_url", "")
            del registry[plugin_name]
            _save_registry(registry)
            _log_install(repo_url, plugin_name, "uninstall")
        enabled = _load_enabled()
        if plugin_name in enabled:
            del enabled[plugin_name]
            _save_enabled(enabled)
        return True

    def check_updates(self) -> dict[str, str]:
        registry = _load_registry()
        updates = {}
        for plugin_name, info in registry.items():
            repo_url = info.get("repo_url", "")
            if not repo_url:
                continue
            try:
                manifests = self.discover(repo_url)
                for m in manifests:
                    if m.get("name") == plugin_name:
                        remote_ver = m.get("version", "0.0.0")
                        local_ver = info.get("version", "0.0.0")
                        if remote_ver != local_ver:
                            updates[plugin_name] = remote_ver
                        break
            except Exception:
                pass
        return updates

    def serve_path(self, plugin_name: str, file_path: str) -> str | None:
        if "/" in plugin_name or "\\" in plugin_name or ".." in plugin_name:
            return None
        base = os.path.join(PLUGINS_DIR, plugin_name)
        target = os.path.normpath(os.path.join(base, file_path))
        if not target.startswith(os.path.normpath(base)):
            return None
        if os.path.exists(target) and os.path.isfile(target):
            return target
        return None

    def get_local_dir(self, plugin_name: str) -> str | None:
        path = os.path.join(PLUGINS_DIR, plugin_name)
        if os.path.isdir(path):
            return path
        return None
