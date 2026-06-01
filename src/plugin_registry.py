"""Plugin registry / depot — discover and install plugins from a curated index.

Like DokuWiki's Extension Manager or Blender's "Get Extensions": a JSON index
lists available plugins with metadata + a download URL; the admin browses it and
installs with one click (download -> verify -> extract into ``plugins/<id>/``).

The default registry is the **upstream** project's, served straight from the main
repo (no server to run):

    https://raw.githubusercontent.com/pewdiepie-archdaemon/odysseus/main/plugins/registry.json

Override with ``ODYSSEUS_PLUGIN_REGISTRY`` (env) or the ``plugin_registry`` app
setting — e.g. to point at a fork while testing, or a private/org registry.

index.json schema (a list of):
    {
      "id": "cloudflare_tunnel",
      "name": "Cloudflare Tunnel",
      "version": "1.0.0",
      "author": "...",
      "category": "Networking",
      "description": "...",
      "download": "https://github.com/<owner>/<repo>/releases/download/v1.0.0/cloudflare_tunnel.zip",
      "sha256": "<hex>",          # optional but recommended; verified before install
      "min_odysseus": "1.0",      # optional
      "homepage": "https://...",  # optional
      "screenshot": "https://..." # optional
    }

The zip must contain the plugin's files at its root (it is extracted into
``plugins/<id>/``). Extraction is hardened against zip-slip / absolute paths.

Security: installing a plugin runs third-party code. These functions are wired to
admin-only routes; downloads are HTTPS-only and sha256-verified when a digest is
provided. Keep the default (curated) registry as the trusted source.
"""
import hashlib
import io
import json
import os
import shutil
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional

from src.plugin_system import plugins_dir, get_manager

DEFAULT_REGISTRY = (
    "https://raw.githubusercontent.com/pewdiepie-archdaemon/odysseus/main/plugins/registry.json"
)


def _allowed_url(url: str) -> bool:
    """Allow https anywhere, or http only to loopback (local/LAN registries +
    testing). Blocks plaintext http to arbitrary hosts (MITM risk)."""
    u = (url or "").lower()
    if u.startswith("https://"):
        return True
    if u.startswith("http://"):
        host = u[len("http://"):].split("/", 1)[0].split(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")
    return False


def _data_root() -> str:
    root = os.environ.get("ODYSSEUS_DATA_DIR")
    if root:
        return root
    try:
        from core.constants import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.path.join(os.path.dirname(plugins_dir()), "data")


def _custom_path() -> str:
    return os.path.join(_data_root(), "plugin_registries.json")


def _load_custom() -> List[str]:
    try:
        with open(_custom_path(), encoding="utf-8") as f:
            return [u for u in json.load(f) if isinstance(u, str)]
    except Exception:
        return []


def _save_custom(urls: List[str]) -> None:
    path = _custom_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=2)


def get_registries() -> List[str]:
    """Ordered, de-duplicated registry URLs to aggregate. Base = the env override
    (comma-separated) or the ``plugin_registry`` setting or the upstream default;
    user-added custom registries (managed in the UI) are appended."""
    out: List[str] = []
    env = (os.environ.get("ODYSSEUS_PLUGIN_REGISTRY") or "").strip()
    if env:
        out += [u.strip() for u in env.split(",") if u.strip()]
    else:
        try:
            from src.settings import get_setting
            out += [u.strip() for u in (get_setting("plugin_registry", "") or "").split(",") if u.strip()]
        except Exception:
            pass
        if not out:
            out.append(DEFAULT_REGISTRY)
    out += _load_custom()
    seen, result = set(), []
    for u in out:
        if u and u not in seen and _allowed_url(u):
            seen.add(u)
            result.append(u)
    return result


def registry_url() -> str:
    """The primary registry (first source) — used for display."""
    regs = get_registries()
    return regs[0] if regs else DEFAULT_REGISTRY


def add_registry(url: str) -> List[str]:
    if not _allowed_url(url):
        raise ValueError("registry URL must be https (or http to loopback)")
    custom = _load_custom()
    if url not in custom:
        custom.append(url)
        _save_custom(custom)
    return get_registries()


def remove_registry(url: str) -> List[str]:
    _save_custom([u for u in _load_custom() if u != url])
    return get_registries()


def fetch_registry(url: Optional[str] = None, timeout: int = 15) -> List[Dict[str, Any]]:
    """Fetch + parse a single registry index. Raises on network/parse error."""
    url = url or registry_url()
    if not _allowed_url(url):
        raise ValueError("registry URL must be https (or http to loopback)")
    with urllib.request.urlopen(url, timeout=timeout) as r:  # nosec - admin-configured
        data = json.loads(r.read().decode("utf-8"))
    if isinstance(data, dict) and isinstance(data.get("plugins"), list):
        data = data["plugins"]
    if not isinstance(data, list):
        raise ValueError("registry must be a JSON list (or {\"plugins\": [...]})")
    return data


def find_entry(plugin_id: str) -> Optional[Dict[str, Any]]:
    """Find a plugin's entry across all registries (first match wins)."""
    for url in get_registries():
        try:
            for e in fetch_registry(url):
                if e.get("id") == plugin_id:
                    return e
        except Exception:
            continue
    return None


def available() -> Dict[str, Any]:
    """Aggregate entries across all registries, annotated with install state.
    Returns ``{"plugins": [...], "sources": [{"url", "ok", "count"/"error"}]}``."""
    mgr = get_manager()
    installed = {p["id"]: p for p in (mgr.list() if mgr else [])}
    seen, plugins, sources = set(), [], []
    for url in get_registries():
        try:
            entries = fetch_registry(url)
            sources.append({"url": url, "ok": True, "count": len(entries)})
        except Exception as e:
            sources.append({"url": url, "ok": False, "error": str(e)})
            continue
        for e in entries:
            pid = e.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            e = dict(e)
            e["_source"] = url
            cur = installed.get(pid)
            if cur:
                e["installed"] = True
                e["installed_version"] = cur.get("version", "")
                e["update_available"] = bool(e.get("version") and cur.get("version") and
                                              _ver(e["version"]) > _ver(cur["version"]))
            else:
                e["installed"] = False
            plugins.append(e)
    return {"plugins": plugins, "sources": sources}


def _ver(v: str):
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _safe_extract(zf: zipfile.ZipFile, dest: str) -> None:
    """Extract ``zf`` into ``dest``, rejecting zip-slip (absolute paths / ``..``
    that escape dest) and symlinks."""
    dest_real = os.path.realpath(dest)
    for member in zf.infolist():
        name = member.filename
        if name.endswith("/"):
            continue
        target = os.path.realpath(os.path.join(dest, name))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise ValueError(f"unsafe path in archive: {name}")
        # block symlinks (high bits of external_attr encode the unix mode)
        mode = (member.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"symlink not allowed in archive: {name}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def install(entry: Optional[Dict[str, Any]] = None, *, url: Optional[str] = None,
            plugin_id: Optional[str] = None, sha256: Optional[str] = None,
            timeout: int = 60) -> Dict[str, Any]:
    """Install a plugin from the registry (pass ``entry``) or a direct zip URL
    (pass ``url`` + ``plugin_id``). Downloads, verifies sha256 (if given), and
    extracts into ``plugins/<id>/`` (replacing any existing copy), then rescans +
    enables. Returns the manager's record for the plugin."""
    if entry:
        url = entry.get("download")
        plugin_id = entry.get("id")
        sha256 = entry.get("sha256") or sha256
    if not url or not plugin_id:
        raise ValueError("need a download url + plugin id")
    if not _allowed_url(url):
        raise ValueError("download URL must be https (or http to loopback)")
    if not _is_safe_id(plugin_id):
        raise ValueError("invalid plugin id")

    with urllib.request.urlopen(url, timeout=timeout) as r:  # nosec - admin action
        blob = r.read()
    if sha256:
        got = hashlib.sha256(blob).hexdigest()
        if got.lower() != sha256.lower():
            raise ValueError(f"sha256 mismatch (expected {sha256}, got {got})")

    target = os.path.join(plugins_dir(), plugin_id)
    staging = target + ".incoming"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            _safe_extract(zf, staging)
        # a zip that wraps everything in a single top dir → flatten it
        _flatten_single_root(staging)
        if not _has_plugin_entry(staging):
            raise ValueError("archive has no plugin.py / *_plugin.py")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    mgr = get_manager()
    if mgr:
        mgr.load_enabled()            # rescan picks up the new folder
        try:
            return mgr.enable(plugin_id)
        except KeyError:
            pass
    return {"id": plugin_id, "installed": True}


def uninstall(plugin_id: str) -> Dict[str, Any]:
    """Disable + delete a plugin's folder. Admin-only at the route layer."""
    if not _is_safe_id(plugin_id):
        raise ValueError("invalid plugin id")
    mgr = get_manager()
    if mgr:
        try:
            mgr.disable(plugin_id)
        except KeyError:
            pass
    folder = os.path.join(plugins_dir(), plugin_id)
    single = os.path.join(plugins_dir(), plugin_id + "_plugin.py")
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    elif os.path.isfile(single):
        os.remove(single)
    else:
        raise KeyError(plugin_id)
    if mgr:
        mgr.discover()
    return {"id": plugin_id, "removed": True}


def _is_safe_id(pid: str) -> bool:
    import re
    return bool(pid) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", pid) is not None


def _has_plugin_entry(folder: str) -> bool:
    if os.path.isfile(os.path.join(folder, "plugin.py")):
        return True
    return any(n.endswith("_plugin.py") for n in os.listdir(folder))


def _flatten_single_root(folder: str) -> None:
    entries = os.listdir(folder)
    if len(entries) == 1 and os.path.isdir(os.path.join(folder, entries[0])):
        inner = os.path.join(folder, entries[0])
        for n in os.listdir(inner):
            shutil.move(os.path.join(inner, n), os.path.join(folder, n))
        os.rmdir(inner)
