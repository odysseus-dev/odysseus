"""MCP _send_email envelope must not split inside comma'd display names.

_send_email built the SMTP envelope with to.split(","), so a recipient like
\'"Smith, John" <john@corp.com>\' became [\'"Smith\', \'John" <john@corp.com>\'],
producing an invalid RCPT TO and a malformed second recipient -> bounce. The
email_routes path was fixed (_envelope_recipients) but the MCP server twin
was missed. getaddresses parses the list correctly.
"""
import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


def test_comma_in_display_name_is_not_split():
    out = es._envelope_recipients('"Smith, John" <john@corp.com>, Alice <a@x.com>')
    assert out == ["john@corp.com", "a@x.com"]


def test_cc_and_bcc_lists_and_strings():
    out = es._envelope_recipients("a@x.com", cc=["b@y.com", "c@z.com"], bcc="d@w.com")
    assert out == ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]


def test_plain_addresses_unchanged():
    assert es._envelope_recipients("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]


def test_empty_fields_ignored():
    assert es._envelope_recipients("a@x.com", cc=None, bcc="") == ["a@x.com"]
