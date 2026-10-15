"""Tests for OXS registry (install / enable / disable / reload / uninstall)."""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from core.extensions import (
    OXSManifestError,
    ExtensionRegistry,
)


def _write_user_ext(
    user_dir: str,
    ext_id: str,
    *,
    plugin_src: str = "def register(host):\n    host.get_logger().info('hello')\n",
    manifest_extra: dict | None = None,
) -> str:
    """Create a minimal user-extension dir under ``user_dir/<ext_id>``."""
    sub = os.path.join(user_dir, ext_id)
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin_src)
    manifest = {
        "id": ext_id,
        "name": ext_id,
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {},
        "file_hashes": {},
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    return sub


def _registry(
    tmp_path,
    bundled_dir: str | None = None,
    user_dir: str | None = None,
) -> ExtensionRegistry:
    return ExtensionRegistry(
        bundled_dir=bundled_dir or str(tmp_path / "bundled"),
        user_dir=user_dir or str(tmp_path / "user"),
    )


# ── Discovery → rescan ──────────────────────────────────────────────────────


def test_rescan_finds_bundled(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext-a")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    found = reg.rescan()
    assert [e.extension_id for e in found] == ["ext-a"]


def test_rescan_idempotent(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext-a")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.rescan()
    assert len(reg.list()) == 1


def test_rescan_picks_up_newly_added(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext-a")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    _write_user_ext(str(bundled), "ext-b")
    reg.rescan()
    assert sorted(e.extension_id for e in reg.list()) == ["ext-a", "ext-b"]


# ── Enable ─────────────────────────────────────────────────────────────────


def test_enable_runs_register_and_lifecycle(tmp_path):
    bundled = tmp_path / "bundled"
    sub = _write_user_ext(
        str(bundled), "ext",
        plugin_src=(
            "from core.extensions import on_enable, on_disable\n"
            "def register(host):\n"
            "    calls = []\n"
            "    @host.on_enable\n"
            "    def _e():\n"
            "        calls.append('enabled')\n"
            "    @host.on_disable\n"
            "    def _d():\n"
            "        calls.append('disabled')\n"
            "    host.on_enable(lambda: calls.append('decorated'))\n"
        ),
    )
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.enable("ext")
    assert reg.get("ext").enabled is True
    reg.disable("ext")
    assert reg.get("ext").enabled is False


def test_enable_raises_keyerror_for_unknown(tmp_path):
    reg = _registry(tmp_path)
    with pytest.raises(KeyError):
        reg.enable("nonexistent")


def test_enable_idempotent(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.enable("ext")
    first_host = reg.get("ext").host
    reg.enable("ext")  # should be no-op
    assert reg.get("ext").host is first_host


def test_enable_records_capability_violations(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(
        str(bundled), "ext",
        plugin_src=(
            "def register(host):\n"
            "    # undeclared capability\n"
            "    host.add_route('/x', lambda: None)\n"
        ),
        manifest_extra={"capabilities": {}},  # empty
    )
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    with pytest.raises(Exception):
        reg.enable("ext")
    assert reg.get("ext").host.violations


def test_disable_clears_registrations(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(
        str(bundled), "ext",
        plugin_src=(
            "def register(host):\n"
            "    host.register_tool('hi', {'type':'object'}, lambda a, c: None)\n"
            "    host.add_route('/x', lambda: None, auth='user')\n"
        ),
        manifest_extra={"capabilities": {"tools": True, "routes": True}},
    )
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.enable("ext")
    assert len(reg.get("ext").host.tools) == 1
    assert len(reg.get("ext").host.routes) == 1
    reg.disable("ext")
    assert reg.get("ext").host.tools == []
    assert reg.get("ext").host.routes == []


def test_disable_noop_for_already_disabled(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.disable("ext")  # never enabled — must not raise


# ── Reload ─────────────────────────────────────────────────────────────────


def test_reload_re_enables_when_was_enabled(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(
        str(bundled), "ext",
        plugin_src="def register(host):\n    host.register_tool('hi', {}, lambda a, c: None)\n",
        manifest_extra={"capabilities": {"tools": True}},
    )
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.enable("ext")
    reg.reload("ext")
    assert reg.get("ext").enabled is True
    assert len(reg.get("ext").host.tools) == 1


def test_reload_after_disabled_refreshes_manifest(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.reload("ext")  # never enabled
    assert reg.get("ext") is not None


# ── Uninstall ──────────────────────────────────────────────────────────────


def test_uninstall_removes_from_registry(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.uninstall("ext")
    assert "ext" not in reg


def test_uninstall_disables_first(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(
        str(bundled), "ext",
        plugin_src="def register(host): pass",
    )
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    reg.enable("ext")
    reg.uninstall("ext")
    assert "ext" not in reg


def test_uninstall_unknown_is_noop(tmp_path):
    reg = _registry(tmp_path)
    reg.uninstall("nonexistent")  # must not raise


# ── Real hello-odysseus ────────────────────────────────────────────────────


def test_hello_odysseus_full_cycle(tmp_path):
    """Bundled reference extension must discover + enable + disable cleanly."""
    import os
    bundled = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "extensions",
    ))
    reg = ExtensionRegistry(bundled_dir=bundled, user_dir=str(tmp_path / "user"))
    reg.rescan()
    assert "hello-odysseus" in reg

    reg.enable("hello-odysseus")
    running = reg.get("hello-odysseus")
    assert running.enabled
    assert len(running.host.statics) == 1
    assert running.host.statics[0].url_prefix == "/ext-static/hello-odysseus"
    assert len(running.host.event_subs) == 1
    assert len(running.host.event_fires_during_enable) == 1

    reg.disable("hello-odysseus")
    assert not running.enabled
    assert running.host.statics == []
    assert running.host.event_subs == []
    assert running.host.event_fires_during_enable == []


# ── Introspection ──────────────────────────────────────────────────────────


def test_contains_and_get(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "ext")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    assert "ext" in reg
    assert reg.get("ext") is not None
    assert reg.get("nonexistent") is None


def test_list_and_list_enabled(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "a")
    _write_user_ext(str(bundled), "b")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    assert sorted(e.extension_id for e in reg.list()) == ["a", "b"]
    assert reg.list_enabled() == []
    reg.enable("a")
    assert [e.extension_id for e in reg.list_enabled()] == ["a"]


def test_iteration(tmp_path):
    bundled = tmp_path / "bundled"
    _write_user_ext(str(bundled), "a")
    reg = _registry(tmp_path, bundled_dir=str(bundled))
    reg.rescan()
    ids = [e.extension_id for e in reg]
    assert ids == ["a"]