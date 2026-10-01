"""Regression coverage for the Microsoft OAuth wiring in the email account forms.

`static/js/settings.js` renders two email account forms — the Settings account
form (`eaf-` ids) and the first-run setup form (`uf-` ids). Both must offer the
Microsoft connect flow for the Outlook / Office 365 preset, and neither may
hardcode Google as the provider the connect button authorizes against.

The module pulls in the DOM and a dozen sibling modules, so these read the
source rather than booting it — the same idiom as
`test_note_reminder_email_oauth.py` for this file.
"""

from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source():
    return (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")


def test_outlook_preset_is_marked_as_a_microsoft_oauth_provider(source):
    """Both provider tables must flag Outlook as OAuth, which is what shows the
    connect panel and hides the password fields."""
    # `outlook:` also keys a provider-logo map and a provider-note map, so
    # match only the preset rows, which carry the IMAP settings.
    outlook_rows = [
        line for line in source.splitlines()
        if line.strip().startswith("outlook:") and "imap: {" in line
    ]
    assert len(outlook_rows) == 2, "expected the Outlook preset in both forms"
    for row in outlook_rows:
        assert "oauth: 'microsoft'" in row
        assert "outlook.office365.com" in row
        assert "smtp.office365.com" in row


def test_provider_metadata_covers_both_oauth_providers(source):
    meta = source[source.index("const OAUTH_PROVIDER_META"):]
    meta = meta[:meta.index("};") + 2]
    assert "google:" in meta
    assert "microsoft:" in meta
    assert "Microsoft" in meta


def test_connect_button_authorizes_the_selected_provider(source):
    """The authorize URL must be built from the selected preset. Hardcoding
    `google` would send an Outlook user through the wrong provider's flow."""
    redirects = [
        line for line in source.splitlines()
        if "/api/email/oauth/" in line and "authorize?account_id=" in line
    ]
    assert len(redirects) == 2, "expected one connect redirect per account form"
    for line in redirects:
        assert "/api/email/oauth/${encodeURIComponent(provider)}/authorize" in line
        assert "/api/email/oauth/google/authorize" not in line


def test_connect_panel_copy_is_not_hardcoded_to_google(source):
    assert "Connect with Google<" not in source
    assert "'Reconnect with Google'" not in source
    assert "✓ Connected via Google OAuth'" not in source


def test_both_forms_restore_the_panel_for_a_connected_microsoft_account(source):
    """Reopening a connected Outlook account must show its panel, otherwise the
    account looks unconnected and its password fields reappear."""
    assert source.count("oauth_provider === 'microsoft') _syncOauthUI('outlook')") == 2


def test_send_capability_accepts_any_oauth_provider(source):
    """OAuth accounts store no SMTP password, so `oauth_provider` alone marks
    them send-capable — for Microsoft as well as Google."""
    helper = source[source.index("const smtpAccountReady"):]
    helper = helper[:helper.index(");") + 2]
    assert "!!account.oauth_provider" in helper
    assert "=== 'google'" not in helper


def test_outlook_provider_note_points_at_the_connect_flow(source):
    """The note used to say Microsoft OAuth was unsupported; it must now tell
    the user what to do instead."""
    assert "does not support Microsoft OAuth" not in source
    # The note renders under the Provider dropdown and the connect panel sits
    # further down the form, so it must point the reader downwards.
    assert source.count('the "Connect with Microsoft" button below') == 2


def test_single_tenant_endpoint_error_is_explained(source):
    """AADSTS50194 is the failure a single-tenant app registration hits against
    the default /common endpoint, and the fix is a config change the operator
    can make — so the panel has to name it rather than say "OAuth failed"."""
    guidance = source[source.index("const OAUTH_PROVIDER_CODE_GUIDANCE"):]
    guidance = guidance[:guidance.index("};") + 2]
    assert "AADSTS50194" in guidance
    assert "MICROSOFT_OAUTH_TENANT_ID" in guidance
