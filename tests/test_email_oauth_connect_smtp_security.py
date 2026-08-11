"""Regression coverage for SMTP security saved before Google OAuth."""

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]


def test_email_tab_oauth_connect_persists_selected_smtp_security():
    source = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    start = source.index("el('eaf-oauth-btn').addEventListener")
    handler_body = source[start:source.index("if (!body.name)", start)]

    assert "smtp_security: el('eaf-smtp-security').value" in handler_body
    assert "display_name: el('eaf-display-name').value.trim()" in handler_body
