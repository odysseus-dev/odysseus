import pytest
from fastapi import BackgroundTasks

"""Regression: SMTP envelope recipients must be parsed, not split on bare commas.

The send paths built the RCPT TO list with `field.split(",")`, which corrupts a
display name containing a comma (e.g. `"Smith, John" <john@corp.com>`, the common
Outlook / corporate address-book form): it splits into `"Smith` and
`John" <john@corp.com>`, so the broken fragments are handed to smtp.sendmail and
delivery fails. `_envelope_recipients` uses email.utils.getaddresses instead.

Also covered: the /send route must surface a clear "No valid recipient address"
error when the envelope resolves to empty (no SMTP server call, no opaque "{}"
SMTPRecipientsRefused leaked to the user).
"""
import routes.email_routes as email_routes
from routes.email_helpers import SendEmailRequest


def test_display_name_with_comma_yields_one_address():
    assert email_routes._envelope_recipients('"Smith, John" <john@corp.com>') == ["john@corp.com"]


def test_multiple_plain_addresses():
    assert email_routes._envelope_recipients("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]


def test_to_cc_bcc_combined_and_none_safe():
    got = email_routes._envelope_recipients('"Doe, Jane" <jane@x.com>, bob@y.com', None, "carol@z.com")
    assert got == ["jane@x.com", "bob@y.com", "carol@z.com"]


def test_empty_and_none_fields():
    assert email_routes._envelope_recipients("", None) == []


def test_trailing_comma_does_not_drop_address():
    # Regression for #4562: a recipient ending in a stray "," used to be
    # dropped by email.utils.getaddresses on older Python, leaving the
    # envelope empty and the send failing with an opaque "{}" error.
    # _envelope_recipients now normalises each field first.
    assert email_routes._envelope_recipients("someone@example.com,") == ["someone@example.com"]
    assert email_routes._envelope_recipients("someone@example.com, ") == ["someone@example.com"]
    assert email_routes._envelope_recipients(" , someone@example.com") == ["someone@example.com"]
    assert email_routes._envelope_recipients("a@x.com, b@y.com,") == ["a@x.com", "b@y.com"]


def test_display_name_with_comma_preserved_through_normalize():
    # The normaliser must not split display names that contain a comma.
    # "Smith, John" is a single token from the address-book's point of view.
    got = email_routes._envelope_recipients('"Smith, John" <john@corp.com>,')
    assert got == ["john@corp.com"]


def test_all_fields_empty_or_garbage_yields_empty_envelope():
    # After normalisation, fields that contain only separator junk must
    # produce an empty envelope (so the caller can surface a clear error
    # rather than the opaque "{}" SMTPRecipientsRefused).
    assert email_routes._envelope_recipients("", "  ", " , ") == []
    assert email_routes._envelope_recipients(None, None) == []


@pytest.mark.asyncio
async def test_send_email_returns_clear_error_on_empty_envelope(monkeypatch):
    """Regression for #4562: an unparseable/empty To should not bubble up the
    opaque "{}" SMTPRecipientsRefused from smtp.sendmail. /send must return
    a clear error and never reach _send_smtp_message."""
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    send_endpoint = None
    for route in router.routes:
        if route.path == "/api/email/send" and "POST" in getattr(route, "methods", set()):
            send_endpoint = route.endpoint
            break
    assert send_endpoint is not None, "email /send route not found"

    # A valid SMTP config (so the route reaches the envelope guard, not
    # the "no account configured" guard).
    monkeypatch.setattr(
        "routes.email_routes._resolve_send_config",
        lambda account_id, owner="": {
            "account_id": "acct-test",
            "from_address": "me@example.com",
            "display_name": "Me",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_user": "me",
            "smtp_password": "x",
        },
    )

    smtp_called = {"count": 0}
    def _fail_if_called(*a, **kw):
        smtp_called["count"] += 1
        raise AssertionError("_send_smtp_message must not be called for empty envelope")
    monkeypatch.setattr("routes.email_routes._send_smtp_message", _fail_if_called)

    req = SendEmailRequest(
        to=" , ,",  # only separator junk -> empty envelope
        cc=None,
        bcc=None,
        subject="hello",
        body="body",
    )
    bg = BackgroundTasks()
    result = await send_endpoint(req=req, background_tasks=bg, owner="alice")

    assert result == {"success": False, "error": "No valid recipient address"}
    assert smtp_called["count"] == 0
