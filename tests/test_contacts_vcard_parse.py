"""Regression: _parse_vcards must read Apple/iCloud item-grouped properties.

RFC 6350 property groups (the default emitted by Apple Contacts.app / iCloud and
many CardDAV servers) prefix the property name with a group token, e.g.
`item1.EMAIL;type=pref:jane@example.com`. The parser matched property names with
a bare `line.startswith("EMAIL")` / `"TEL"` / `"FN:"`, so grouped lines never
matched and the email / phone were silently dropped — breaking contact search by
email, the email-composer autocomplete, and vCard/CSV export round-trips for any
address book synced from Apple.
"""
from routes.contacts_routes import _parse_vcards


def test_apple_item_grouped_properties_parsed():
    vcf = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Jane Doe\n"
        "item1.EMAIL;type=INTERNET;type=pref:jane@example.com\n"
        "item2.TEL;type=CELL;type=pref:+15550100\n"
        "UID:abc-123\nEND:VCARD\n"
    )
    c = _parse_vcards(vcf)[0]
    assert c["emails"] == ["jane@example.com"]
    assert c["phones"] == ["+15550100"]
    assert c["uid"] == "abc-123"


def test_plain_ungrouped_properties_still_parsed():
    vcf = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:John Smith\n"
        "EMAIL;TYPE=INTERNET:john@example.com\n"
        "TEL;TYPE=CELL:+15550199\n"
        "UID:xyz\nEND:VCARD\n"
    )
    c = _parse_vcards(vcf)[0]
    assert c["name"] == "John Smith"
    assert c["emails"] == ["john@example.com"]
    assert c["phones"] == ["+15550199"]
    assert c["uid"] == "xyz"
