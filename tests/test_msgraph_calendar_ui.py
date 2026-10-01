"""Regression coverage for the Microsoft 365 calendar wiring in settings.js.

The module pulls in the DOM and a dozen sibling modules, so these read the
source rather than booting it — the same idiom as
`test_email_oauth_microsoft_ui.py` for this file.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source():
    return (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")


# ── Integration card ──────────────────────────────────────────────

def test_microsoft_calendar_is_an_integration_type(source):
    assert re.search(r"msgraph:\s*\{\s*label:\s*'Microsoft 365'", source)


def test_connected_calendars_are_fetched_for_the_list(source):
    assert "fetch('/api/calendar/config/microsoft'" in source


def test_each_connected_mailbox_gets_a_card(source):
    assert "type: 'msgraph'," in source


def test_microsoft_calendar_is_offered_in_the_add_menu(source):
    assert "['msgraph', 'Microsoft 365 Calendar']" in source


def test_the_add_menu_opens_the_connect_form(source):
    assert "else if (type === 'msgraph') showMsGraphForm();" in source


def test_removing_a_card_disconnects_the_account(source):
    assert "`/api/calendar/config/microsoft/${id}`" in source
    assert "method: 'DELETE'" in source


# ── Connect form ──────────────────────────────────────────────────

def test_connect_sends_the_browser_to_the_authorize_route(source):
    assert "'/api/calendar/oauth/microsoft/authorize'" in source


def test_the_form_never_asks_for_a_microsoft_password(source):
    """The whole point of OAuth here: the password never reaches Odysseus."""
    form = source.split("async function showMsGraphForm()")[1].split("async function showCalDavForm")[0]
    assert "type=\"password\"" not in form
    assert "uf-msgraph-pass" not in form


def test_connect_is_disabled_until_the_app_registration_exists(source):
    """Without a client id the redirect only reaches a Microsoft error page."""
    form = source.split("async function showMsGraphForm()")[1].split("async function showCalDavForm")[0]
    assert "configured ? '' : 'disabled'" in form
    assert "MICROSOFT_OAUTH_CLIENT_ID is not set" in form
    assert "if (!configured) return;" in form


def test_the_form_names_the_graph_permission_that_is_required(source):
    form = source.split("async function showMsGraphForm()")[1].split("async function showCalDavForm")[0]
    assert "Calendars.ReadWrite" in form


def test_the_notice_is_escaped_before_it_reaches_the_markup(source):
    form = source.split("async function showMsGraphForm()")[1].split("async function showCalDavForm")[0]
    assert "${esc(notice)}" in form


# ── OAuth result banner ───────────────────────────────────────────

def test_the_result_banner_handles_both_oauth_flows(source):
    """Mail and calendar report back the same way under their own prefix."""
    assert "{ prefix: 'email_oauth', subject: 'email' }" in source
    assert "{ prefix: 'calendar_oauth', subject: 'calendar sync' }" in source


def test_the_banner_reads_params_by_flow_prefix_not_a_hardcoded_one(source):
    for suffix in ("success", "error", "provider", "code", "aadsts"):
        assert f"${{flow.prefix}}_{suffix}" in source


def test_calendar_only_failures_carry_guidance(source):
    assert "no_refresh_token:" in source
    assert "microsoft_error:" in source


def test_the_scope_guidance_covers_the_calendar_permission(source):
    """A missing Graph permission and a missing Exchange one both land on
    invalid_scope, so the text has to name each."""
    line = next(l for l in source.splitlines() if l.strip().startswith("invalid_scope:"))
    assert "Calendars.ReadWrite" in line
    assert "IMAP.AccessAsUser.All" in line
