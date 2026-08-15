"""Regression coverage for the settings UI after Google OAuth redirects."""

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]


def test_oauth_redirect_uses_the_module_local_settings_api():
    source = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    handler = source[
        source.index("(function _handleOauthRedirect"):
        source.index("const settingsModule =")
    ]

    assert "open('integrations');" in handler
    assert "window.settingsModule" not in handler
    assert "window.__odysseusAppStarted" not in handler
    assert "document.addEventListener('DOMContentLoaded', _showResult, { once: true })" in handler
