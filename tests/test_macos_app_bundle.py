"""Regression tests for the generated macOS launcher bundle."""

from pathlib import Path
import plistlib
import re


ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "build-macos-app.sh"


def _build_script() -> str:
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _generated_info_plist() -> dict:
    script = _build_script()
    match = re.search(
        r'cat > "\$APP/Contents/Info\.plist" <<PLIST\n(.*?)\nPLIST',
        script,
        re.DOTALL,
    )
    assert match, "could not find the Info.plist heredoc"
    rendered = match.group(1).replace("$APP_NAME", "Odysseus")
    return plistlib.loads(rendered.encode())


def test_bundle_declares_protected_install_folder_access():
    plist = _generated_info_plist()

    for key in (
        "NSDocumentsFolderUsageDescription",
        "NSDesktopFolderUsageDescription",
        "NSDownloadsFolderUsageDescription",
    ):
        assert plist.get(key), f"generated Info.plist is missing {key}"


def test_launcher_checks_python_environment_access_before_starting_uvicorn():
    script = _build_script()
    probe = 'if ! /bin/cat "$PYVENV_CFG" >/dev/null 2>&1; then'
    launch = '"$UVICORN" app:app'

    assert probe in script
    assert 'Expand “Odysseus”.' in script
    assert "There is no Add button in Files & Folders" in script
    assert script.index(probe) < script.index(launch)


def test_bundle_uses_native_executable_for_macos_privacy_identity():
    script = _build_script()

    assert 'Contents/Resources/$APP_NAME-launcher' in script
    assert 'xcrun clang' in script
    assert '-o "$APP/Contents/MacOS/$APP_NAME"' in script
    assert 'NSTask *launcherTask' in script
    assert 'codesign --force --deep --sign - "$APP"' in script


def test_native_launcher_has_menu_bar_status_and_exit_action():
    script = _build_script()
    plist = _generated_info_plist()

    assert plist["LSUIElement"] is True
    assert "NSStatusBar systemStatusBar" in script
    assert '@"Odysseus is running"' in script
    assert '@"Exit Odysseus"' in script
    assert "[self.launcherTask terminate]" in script
    assert "disableAutomaticTermination" in script
    assert "static OdysseusDelegate *delegate" in script
