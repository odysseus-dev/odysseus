"""_parse_vcards must unfold RFC 6350 folded lines.

A long property value is folded by inserting a line break + a single space/tab.
The parser split on newlines without unfolding, so a folded EMAIL/TEL/FN was
truncated at the fold and the continuation (which does not start with a property
name) was dropped.
"""
from routes.contacts_routes import _parse_vcards


def test_folded_email_is_unfolded():
    vcf = "BEGIN:VCARD\nFN:Test\nEMAIL:verylongmailbox-part1\n part2@example.com\nEND:VCARD\n"
    contacts = _parse_vcards(vcf)
    assert contacts[0]["emails"] == ["verylongmailbox-part1part2@example.com"]


def test_folded_email_crlf_and_tab():
    vcf = "BEGIN:VCARD\r\nFN:Test\r\nEMAIL:abc\r\n\tdef@example.com\r\nEND:VCARD\r\n"
    contacts = _parse_vcards(vcf)
    assert contacts[0]["emails"] == ["abcdef@example.com"]


def test_unfolded_vcard_still_parses():
    vcf = "BEGIN:VCARD\nFN:Jane Doe\nEMAIL:jane@example.com\nTEL:+15551234567\nEND:VCARD\n"
    contacts = _parse_vcards(vcf)
    assert contacts[0]["name"] == "Jane Doe"
    assert contacts[0]["emails"] == ["jane@example.com"]
    assert contacts[0]["phones"] == ["+15551234567"]
