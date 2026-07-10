"""Regression coverage for Windows installer icons and launch shortcuts."""

import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
BUILD = PACKAGE["build"]
MAIN_JS = (ROOT / "main.js").read_text(encoding="utf-8")
INSTALLER_SPEC = (ROOT / "installer.spec").read_text(encoding="utf-8")
INSTALLER_NSH = (ROOT / "installer.nsh").read_text(encoding="utf-8")
PORTABLE_BUILD = (ROOT / "build-windows-portable.ps1").read_text(encoding="utf-8")


def _ico_sizes(path: Path) -> set[int]:
    raw = path.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", raw, 0)
    assert (reserved, image_type) == (0, 1)
    sizes = set()
    for index in range(count):
        width, height = struct.unpack_from("<BB", raw, 6 + (index * 16))
        normalized_width = width or 256
        normalized_height = height or 256
        assert normalized_width == normalized_height
        sizes.add(normalized_width)
    return sizes


def test_windows_icon_contains_shell_and_high_resolution_sizes():
    sizes = _ico_sizes(ROOT / "static" / "icon.ico")
    assert {16, 24, 32, 48, 64, 128, 256}.issubset(sizes)


def test_standalone_installer_creates_and_repairs_launch_shortcuts():
    assert BUILD["win"]["icon"] == "static/icon.ico"
    assert BUILD["nsis"]["createDesktopShortcut"] == "always"
    assert BUILD["nsis"]["createStartMenuShortcut"] is True
    assert BUILD["nsis"]["shortcutName"] == "Simple Signal"
    assert BUILD["nsis"]["include"] == "installer.nsh"
    assert 'CreateShortCut "$newDesktopLink"' in INSTALLER_NSH
    assert 'CreateShortCut "$newStartMenuLink"' in INSTALLER_NSH
    assert INSTALLER_NSH.count("WinShell::SetLnkAUMI") == 2


def test_standalone_app_and_shortcuts_share_windows_app_id():
    app_id = BUILD["appId"]
    assert f"const WINDOWS_APP_USER_MODEL_ID = '{app_id}';" in MAIN_JS
    assert "app.setAppUserModelId(WINDOWS_APP_USER_MODEL_ID);" in MAIN_JS


def test_extensions_installer_embeds_the_windows_icon():
    assert "icon=['static\\\\icon.ico']" in INSTALLER_SPEC


def test_one_off_patch_helper_is_excluded_from_windows_packages():
    scripts_fileset = next(item for item in BUILD["extraFiles"] if isinstance(item, dict) and item.get("from") == "scripts")
    assert "!patch_fix.py" in scripts_fileset["filter"]
    assert "endswith('scripts/patch_fix.py')" in INSTALLER_SPEC
    assert "_internal\\scripts\\patch_fix.py" in PORTABLE_BUILD


def test_source_backups_and_bytecode_caches_are_excluded_from_windows_packages():
    assert "!**/*.bak" in BUILD["extraFiles"]
    assert "!**/*.pyc" in BUILD["extraFiles"]
    assert "!**/__pycache__/**" in BUILD["extraFiles"]
    scripts_fileset = next(item for item in BUILD["extraFiles"] if isinstance(item, dict) and item.get("from") == "scripts")
    assert "!**/*.bak" in scripts_fileset["filter"]
    assert "!**/*.pyc" in scripts_fileset["filter"]
    assert "!**/__pycache__/**" in scripts_fileset["filter"]
    assert "path.endswith('.bak')" in INSTALLER_SPEC
    assert "path.endswith('.pyc')" in INSTALLER_SPEC
    assert "'__pycache__' in parts" in INSTALLER_SPEC
    assert '@(".bak", ".pyc")' in PORTABLE_BUILD
    assert '-Filter "__pycache__"' in PORTABLE_BUILD
