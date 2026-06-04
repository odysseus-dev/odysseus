"""Regression: SMTP envelope recipients must be parsed, not split on bare commas.

The send paths built the RCPT TO list with `field.split(",")`, which corrupts a
display name containing a comma (e.g. `"Smith, John" <john@corp.com>`, the common
Outlook / corporate address-book form): it splits into `"Smith` and
`John" <john@corp.com>`, so the broken fragments are handed to smtp.sendmail and
delivery fails. `_envelope_recipients` uses email.utils.getaddresses instead.
"""
import routes.email_routes as email_routes
import routes.email_pollers as email_pollers
import mcp_servers.email_server as mcp_email_server


def test_display_name_with_comma_yields_one_address():
    assert email_routes._envelope_recipients('"Smith, John" <john@corp.com>') == ["john@corp.com"]


def test_multiple_plain_addresses():
    assert email_routes._envelope_recipients("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]


def test_to_cc_bcc_combined_and_none_safe():
    got = email_routes._envelope_recipients('"Doe, Jane" <jane@x.com>, bob@y.com', None, "carol@z.com")
    assert got == ["jane@x.com", "bob@y.com", "carol@z.com"]


def test_empty_and_none_fields():
    assert email_routes._envelope_recipients("", None) == []


def test_scheduled_email_envelope_uses_comma_aware_parser():
    got = email_pollers._envelope_recipients('"Doe, Jane" <jane@x.com>, bob@y.com', None, "carol@z.com")
    assert got == ["jane@x.com", "bob@y.com", "carol@z.com"]


def test_mcp_send_email_uses_comma_aware_envelope_parser(monkeypatch):
    sent = {}

    class FakeSMTP:
        def send_message(self, msg, from_addr=None, to_addrs=None):
            sent["from_addr"] = from_addr
            sent["to_addrs"] = list(to_addrs)

        def quit(self):
            sent["quit"] = True

    monkeypatch.setattr(
        mcp_email_server,
        "_resolve_send_config",
        lambda account: (
            "acct",
            {
                "from_address": "sender@example.com",
                "account_name": "Work",
                "account_id": "acct1",
            },
        ),
    )
    monkeypatch.setattr(mcp_email_server, "_smtp_connect", lambda send_account, cfg=None: FakeSMTP())
    monkeypatch.setattr(
        mcp_email_server,
        "_imap_connect",
        lambda account: (_ for _ in ()).throw(RuntimeError("no imap")),
    )

    result = mcp_email_server._send_email(
        to='"Doe, Jane" <jane@x.com>, bob@y.com',
        cc='"Boss, Bob" <boss@z.com>',
        bcc="hidden@z.com",
        subject="Hello",
        body="Body",
    )

    assert sent == {
        "from_addr": "sender@example.com",
        "to_addrs": ["jane@x.com", "bob@y.com", "boss@z.com", "hidden@z.com"],
        "quit": True,
    }
    assert result["to"] == sent["to_addrs"]
