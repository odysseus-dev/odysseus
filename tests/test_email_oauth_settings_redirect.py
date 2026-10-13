"""Regression coverage for the settings UI after Google OAuth redirects."""

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_HANDLER = _REPO / "static" / "js" / "settings" / "oauthReturn.js"
_COORDINATOR = _REPO / "static" / "js" / "settings.js"


def test_oauth_redirect_uses_the_module_local_settings_api():
    handler = _HANDLER.read_text(encoding="utf-8")

    assert "openSettings('integrations');" in handler
    assert "window.settingsModule" not in handler
    assert "window.__odysseusAppStarted" not in handler
    assert "document.addEventListener('DOMContentLoaded', _showResult, { once: true })" in handler


def test_settings_coordinator_hands_the_handler_its_own_open():
    source = _COORDINATOR.read_text(encoding="utf-8")

    # The handler only stays window-free if the coordinator passes its own
    # export in. A stray `window.settingsModule` here would reintroduce the
    # load-order dependency the handler was written to avoid.
    assert "handleSettingsOauthReturn({ openSettings: open });" in source
    assert "from './settings/oauthReturn.js'" in source
