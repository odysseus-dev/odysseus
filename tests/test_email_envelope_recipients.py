"""Regression: SMTP envelope recipients must be parsed, not split on bare commas.

The send paths built the RCPT TO list with `field.split(",")`, which corrupts a
display name containing a comma (e.g. `"Smith, John" <john@corp.com>`, the common
Outlook / corporate address-book form): it splits into `"Smith` and
`John" <john@corp.com>`, so the broken fragments are handed to smtp.sendmail and
delivery fails. `_envelope_recipients` uses email.utils.getaddresses instead.
"""
import routes.email_routes as email_routes


def test_display_name_with_comma_yields_one_address():
    assert email_routes._envelope_recipients('"Smith, John" <john@corp.com>') == ["john@corp.com"]


def test_multiple_plain_addresses():
    assert email_routes._envelope_recipients("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]


def test_to_cc_bcc_combined_and_none_safe():
    got = email_routes._envelope_recipients('"Doe, Jane" <jane@x.com>, bob@y.com', None, "carol@z.com")
    assert got == ["jane@x.com", "bob@y.com", "carol@z.com"]


def test_empty_and_none_fields():
    assert email_routes._envelope_recipients("", None) == []


def test_trailing_comma_is_tolerated():
    # Regression for #4562: a stray trailing comma in the To field made
    # getaddresses emit a malformed tuple and the only recipient was dropped,
    # so SMTP raised SMTPRecipientsRefused({}) and the UI surfaced "{}".
    assert email_routes._envelope_recipients("alice@example.com,") == ["alice@example.com"]


def test_leading_comma_is_tolerated():
    assert email_routes._envelope_recipients(",alice@example.com") == ["alice@example.com"]


def test_trailing_comma_with_display_name():
    assert email_routes._envelope_recipients('"Smith, John" <john@corp.com>,') == ["john@corp.com"]


def test_trailing_comma_across_to_cc_bcc():
    got = email_routes._envelope_recipients("alice@example.com,", "bob@example.com,", "carol@example.com,")
    assert got == ["alice@example.com", "bob@example.com", "carol@example.com"]
