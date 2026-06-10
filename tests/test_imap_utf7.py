"""Coverage for the IMAP modified-UTF-7 codec (RFC 3501 §5.1.3).

Regression for the bug where Cyrillic/Arabic/etc. IMAP folder names were shown
as their raw ``&...-`` wire form instead of readable Unicode.
"""

from src.imap_utf7 import decode_imap_utf7, encode_imap_utf7


def test_decode_cyrillic_example():
    # The "Sent" folder as encoded by a Russian-locale IMAP server.
    assert decode_imap_utf7("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-") == "Отправленные"


def test_decode_ascii_passthrough():
    assert decode_imap_utf7("INBOX") == "INBOX"
    assert decode_imap_utf7("[Gmail]/Sent Mail") == "[Gmail]/Sent Mail"


def test_decode_literal_ampersand():
    # "&-" is the encoding of a literal ampersand.
    assert decode_imap_utf7("Notes &- Drafts") == "Notes & Drafts"
    assert decode_imap_utf7("R&-D") == "R&D"


def test_decode_bytes_input():
    assert decode_imap_utf7(b"&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-") == "Отправленные"


def test_decode_malformed_is_left_verbatim():
    # An unterminated shift sequence must not raise.
    assert decode_imap_utf7("&BB4") == "&BB4"
    # A terminated run of non-Base64 chars is kept verbatim, not dropped to "".
    assert decode_imap_utf7("&!!!-") == "&!!!-"
    assert decode_imap_utf7("box &@#$- end") == "box &@#$- end"


def test_encode_ascii_passthrough():
    assert encode_imap_utf7("INBOX") == "INBOX"
    assert encode_imap_utf7("[Gmail]/Sent Mail") == "[Gmail]/Sent Mail"


def test_encode_literal_ampersand():
    assert encode_imap_utf7("Notes & Drafts") == "Notes &- Drafts"


def test_encode_cyrillic_matches_wire_form():
    assert encode_imap_utf7("Отправленные") == "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"


def test_round_trip_unicode_and_ascii():
    for name in (
        "INBOX",
        "Отправленные",
        "Входящие",
        "垃圾箱",
        "[Gmail]/All Mail",
        "Notes & Drafts",
        "INBOX.Списки",
    ):
        assert decode_imap_utf7(encode_imap_utf7(name)) == name
