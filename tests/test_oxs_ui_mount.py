"""Tests for OXS UI mount (static file serving via FastAPI)."""

from __future__ import annotations

import os

import pytest

from core.extensions import ui_mount


@pytest.fixture(autouse=True)
def _reset():
    ui_mount.reset_for_tests()
    yield
    ui_mount.reset_for_tests()


class _FakeApp:
    """Minimal FastAPI-like app for testing mount/unmount."""

    def __init__(self):
        from fastapi import FastAPI
        self.router = FastAPI().router
        self.mounted: dict[str, str] = {}

    def mount(self, path: str, app, name: str | None = None):
        self.mounted[path] = name or ""
        # Mirror the FastAPI behaviour: push the sub-app onto router.routes.
        self.router.routes.append(app)


def _make_extension_dir(tmp_path, *, with_ui: bool = True) -> tuple[str, str]:
    """Create an extension dir with optional ``ui/`` subdirectory. Returns (plugin_path, ui_dir)."""
    plugin_path = str(tmp_path / "ext")
    os.makedirs(plugin_path)
    if with_ui:
        ui_dir = os.path.join(plugin_path, "ui")
        os.makedirs(ui_dir)
        with open(os.path.join(ui_dir, "index.html"), "w") as f:
            f.write("<html/>")
        return plugin_path, "ui"
    return plugin_path, ""


# ── install ────────────────────────────────────────────────────────────────


def test_install_mounts_static_files(tmp_path):
    app = _FakeApp()
    plugin_path, directory = _make_extension_dir(tmp_path)
    rec = ui_mount.install(
        app, "ext1", "/ext-static/ext1", plugin_path, directory,
    )
    assert rec.extension_id == "ext1"
    assert rec.url_prefix == "/ext-static/ext1"
    assert "/ext-static/ext1" in app.mounted


def test_install_normalizes_url_prefix(tmp_path):
    app = _FakeApp()
    plugin_path, directory = _make_extension_dir(tmp_path)
    rec = ui_mount.install(app, "ext", "static", plugin_path, directory)
    assert rec.url_prefix == "/static"
    rec2 = ui_mount.install(app, "ext", "/static/", plugin_path, directory)
    # second call replaces — same prefix
    assert rec2.url_prefix == "/static"


def test_install_rejects_absolute_directory(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    with pytest.raises(ValueError, match="relative"):
        ui_mount.install(app, "ext", "/x", plugin_path, "/etc")


def test_install_rejects_path_traversal(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        ui_mount.install(app, "ext", "/x", plugin_path, "../escape")


def test_install_rejects_missing_directory(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    with pytest.raises(FileNotFoundError):
        ui_mount.install(app, "ext", "/x", plugin_path, "does_not_exist")


def test_install_rejects_empty_directory(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path, with_ui=False)
    with pytest.raises(ValueError, match="required"):
        ui_mount.install(app, "ext", "/x", plugin_path, "")


def test_install_state_tracks_all_mounts(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext1", "/a", plugin_path, "ui")
    ui_mount.install(app, "ext2", "/b", plugin_path, "ui")
    mounted = ui_mount.list_mounted()
    assert len(mounted) == 2
    ids = {m.extension_id for m in mounted}
    assert ids == {"ext1", "ext2"}


def test_install_replaces_existing_mount_at_same_prefix(tmp_path):
    """Re-installing under the same prefix replaces the old mount."""
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext", "/x", plugin_path, "ui")
    initial_state = ui_mount.list_mounted()
    assert len(initial_state) == 1
    ui_mount.install(app, "ext", "/x", plugin_path, "ui")
    # State still has one entry (replacement, not duplication)
    assert len(ui_mount.list_mounted()) == 1


# ── uninstall ──────────────────────────────────────────────────────────────


def test_uninstall_removes_specific_prefix(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext1", "/a", plugin_path, "ui")
    ui_mount.install(app, "ext2", "/b", plugin_path, "ui")
    n = ui_mount.uninstall(app, "ext1", url_prefix="/a")
    assert n == 1
    mounted = ui_mount.list_mounted()
    assert [m.extension_id for m in mounted] == ["ext2"]


def test_uninstall_removes_all_for_extension(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext1", "/a", plugin_path, "ui")
    ui_mount.install(app, "ext1", "/b", plugin_path, "ui")
    ui_mount.install(app, "ext2", "/c", plugin_path, "ui")
    n = ui_mount.uninstall(app, "ext1")
    assert n == 2
    mounted = ui_mount.list_mounted()
    assert [m.extension_id for m in mounted] == ["ext2"]


def test_uninstall_unknown_returns_zero(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    n = ui_mount.uninstall(app, "nonexistent")
    assert n == 0


def test_uninstall_unknown_prefix_returns_zero(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext", "/a", plugin_path, "ui")
    n = ui_mount.uninstall(app, "ext", url_prefix="/z")
    assert n == 0
    assert len(ui_mount.list_mounted()) == 1


def test_reset_for_tests_clears_state(tmp_path):
    app = _FakeApp()
    plugin_path, _ = _make_extension_dir(tmp_path)
    ui_mount.install(app, "ext", "/a", plugin_path, "ui")
    assert len(ui_mount.list_mounted()) == 1
    ui_mount.reset_for_tests()
    assert ui_mount.list_mounted() == []


# ── resolve directory ────────────────────────────────────────────────────


def test_resolve_directory_against_plugin_path(tmp_path):
    plugin_path = str(tmp_path / "ext")
    os.makedirs(os.path.join(plugin_path, "ui", "deep"))
    from core.extensions.ui_mount import _resolve_directory
    resolved = _resolve_directory(plugin_path, "ui/deep")
    assert os.path.isdir(resolved)
    assert resolved.startswith(plugin_path)


def test_resolve_directory_rejects_path_with_dotdot(tmp_path):
    plugin_path = str(tmp_path / "ext")
    os.makedirs(plugin_path)
    from core.extensions.ui_mount import _resolve_directory
    with pytest.raises(ValueError):
        _resolve_directory(plugin_path, "../foo")