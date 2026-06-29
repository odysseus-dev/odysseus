"""vCard parsing must unfold RFC 6350 folded lines.

CardDAV servers fold logical lines longer than 75 octets onto continuation
lines that begin with a space/tab. _parse_vcards split on raw newlines
without unfolding, so a folded EMAIL/FN line lost its continuation (a long
address like ...@exampledomain<fold>.com was stored as ...@exampledomain),
silently corrupting the contact.
"""
from routes.contacts_routes import _parse_vcards


def test_folded_email_is_reassembled():
    vcard = (
        "BEGIN:VCARD\r\n"
        "VERSION:3.0\r\n"
        "FN:John Doe\r\n"
        "EMAIL;TYPE=INTERNET:john.doe.with.a.very.long.local.part@exampledomain\r\n"
        " .com\r\n"
        "END:VCARD\r\n"
    )
    contacts = _parse_vcards(vcard)
    assert len(contacts) == 1
    assert contacts[0]["emails"] == [
        "john.doe.with.a.very.long.local.part@exampledomain.com"
    ]


def test_folded_display_name_is_reassembled():
    vcard = (
        "BEGIN:VCARD\n"
        "FN:A Very Long Display Name That The Server\n"
        "  Decided To Fold\n"
        "EMAIL:x@y.com\n"
        "END:VCARD\n"
    )
    c = _parse_vcards(vcard)[0]
    assert c["name"] == "A Very Long Display Name That The Server Decided To Fold"


def test_unfolded_vcard_still_parses():
    vcard = "BEGIN:VCARD\nFN:Jane\nEMAIL:jane@z.com\nTEL:+15550001\nEND:VCARD\n"
    c = _parse_vcards(vcard)[0]
    assert c["name"] == "Jane"
    assert c["emails"] == ["jane@z.com"]
    assert c["phones"] == ["+15550001"]
