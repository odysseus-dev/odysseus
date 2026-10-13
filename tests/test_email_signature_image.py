"""The signature image: validation, MIME shape, and when it is withheld.

What these pin:

- the media type comes from the bytes, not from what the uploader claimed,
  because that type is what tells the recipient's client how to decode it;
- the image is an inline part bound to the HTML that references it, which
  is the difference between a rendered logo and a stray attachment;
- the image rides on the signature text: a message the user unsigned in
  the draft does not go out branded anyway.
"""

import base64
import struct
import zlib

import pytest

from src.email_signature import (
    MAX_SIGNATURE_IMAGE_BYTES,
    SIGNATURE_IMAGE_CID,
    SignatureImageError,
    account_signature,
    account_signature_image,
    apply_signature,
    body_has_signature,
    html_with_signature_image,
    normalize_signature_image,
    signature_image_part,
)

SIG = "Ada Lovelace\nAnalytical Engines Ltd"


def _png(width=1, height=1):
    """A real PNG — the validator reads magic bytes, so a stub will not do."""
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG_B64 = base64.b64encode(_png()).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"jpeg payload").decode()
GIF_B64 = base64.b64encode(b"GIF89a" + b"gif payload").decode()


def _cfg(**over):
    cfg = {
        "signature": SIG,
        "signature_enabled": True,
        "signature_image": PNG_B64,
        "signature_image_mime": "image/png",
    }
    cfg.update(over)
    return cfg


# ── Validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (PNG_B64, "image/png"),
    (f"data:image/png;base64,{PNG_B64}", "image/png"),
    (JPEG_B64, "image/jpeg"),
    (GIF_B64, "image/gif"),
])
def test_the_accepted_formats_round_trip(raw, expected):
    data, mime = normalize_signature_image(raw)
    assert mime == expected
    assert base64.b64decode(data)


def test_the_type_is_read_from_the_bytes_not_the_data_url():
    """The declared type ends up in the MIME header telling the recipient's
    client how to decode the part, and an uploader can declare anything."""
    _, mime = normalize_signature_image(f"data:image/gif;base64,{PNG_B64}")
    assert mime == "image/png"


def test_a_wrapped_data_url_still_decodes():
    """Line breaks survive a copy-pasted data URL and break strict base64."""
    wrapped = "\n".join(PNG_B64[i:i + 40] for i in range(0, len(PNG_B64), 40))
    data, mime = normalize_signature_image(f"data:image/png;base64,\n{wrapped}")
    assert mime == "image/png"
    assert data == PNG_B64


def test_nothing_uploaded_stores_nothing():
    assert normalize_signature_image("   ") == (None, None)
    assert normalize_signature_image(None) == (None, None)


def test_a_non_image_is_refused():
    payload = base64.b64encode(b"MZ\x90\x00 this is an executable").decode()
    with pytest.raises(SignatureImageError, match="PNG, JPEG or GIF"):
        normalize_signature_image(payload)


def test_a_non_image_is_refused_even_when_labelled_as_one():
    payload = base64.b64encode(b"MZ\x90\x00 still an executable").decode()
    with pytest.raises(SignatureImageError):
        normalize_signature_image(f"data:image/png;base64,{payload}")


def test_something_that_is_not_base64_is_refused():
    with pytest.raises(SignatureImageError, match="base64"):
        normalize_signature_image("this is not base64 at all !!!")


def test_an_oversized_image_is_refused():
    big = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_SIGNATURE_IMAGE_BYTES
    with pytest.raises(SignatureImageError, match="KB or smaller"):
        normalize_signature_image(base64.b64encode(big).decode())


def test_an_image_at_the_cap_is_accepted():
    """The boundary belongs to the user, not to the error path."""
    filler = MAX_SIGNATURE_IMAGE_BYTES - len(b"\x89PNG\r\n\x1a\n")
    exact = b"\x89PNG\r\n\x1a\n" + b"x" * filler
    data, mime = normalize_signature_image(base64.b64encode(exact).decode())
    assert mime == "image/png"
    assert len(base64.b64decode(data)) == MAX_SIGNATURE_IMAGE_BYTES


def test_the_stored_form_is_canonical():
    """Different uploaders pad and wrap differently; one form goes in."""
    data, _ = normalize_signature_image(f"data:image/png;base64,{PNG_B64}")
    again, _ = normalize_signature_image(data)
    assert data == again == PNG_B64


# ── Reading it back for a send ────────────────────────────────────

def test_the_image_is_read_from_a_config():
    assert account_signature_image(_cfg()) == (PNG_B64, "image/png")


def test_the_switch_governs_the_image_too():
    """`Use signature` turns off the whole block, not just the text."""
    assert account_signature_image(_cfg(signature_enabled=False)) == (None, None)


def test_an_account_with_no_image_yields_nothing():
    assert account_signature_image(_cfg(signature_image="")) == (None, None)


def test_a_missing_media_type_is_recovered_from_the_bytes():
    """A row written before the type was stored still sends its image."""
    assert account_signature_image(_cfg(signature_image_mime=""))[1] == "image/png"


@pytest.mark.parametrize("stored", [
    "not base64",
    base64.b64encode(b"MZ\x90\x00 not an image").decode(),
    "",
])
def test_an_unreadable_image_is_dropped_rather_than_sent(stored):
    """A send path calls this. Raising here would lose the whole message
    over a decoration, so a column holding something unusable has to read
    as "this account has no image"."""
    assert account_signature_image(_cfg(signature_image=stored)) == (None, None)


def test_a_valid_media_type_does_not_excuse_unreadable_bytes():
    """The column is a hint. Trusting it without decoding is what made an
    unreadable image raise while the message was being assembled."""
    assert account_signature_image(
        _cfg(signature_image="not base64", signature_image_mime="image/png")
    ) == (None, None)


# ── The HTML reference ────────────────────────────────────────────

def test_the_image_goes_inside_the_body_element():
    out = html_with_signature_image("<html><body><p>hi</p></body></html>")
    assert out.endswith("</body></html>")
    assert "<p>hi</p>" in out
    assert f"cid:{SIGNATURE_IMAGE_CID}" in out


def test_a_bare_fragment_gets_the_image_appended():
    out = html_with_signature_image("<p>hi</p>")
    assert out.startswith("<p>hi</p>")
    assert f"cid:{SIGNATURE_IMAGE_CID}" in out


def test_the_image_is_width_capped():
    """A large upload must not stretch the message column."""
    assert "max-width" in html_with_signature_image("<p>hi</p>")


def test_the_alt_text_is_empty():
    """A logo next to a name the reader already has in text adds nothing
    read aloud, and 'company logo' on every message is noise."""
    assert 'alt=""' in html_with_signature_image("<p>hi</p>")


# ── The MIME part ─────────────────────────────────────────────────

def test_the_part_carries_the_content_id_in_angle_brackets():
    """RFC 2392: a `cid:` URL references the value inside the brackets."""
    part = signature_image_part(PNG_B64, "image/png")
    assert part.get("Content-ID") == f"<{SIGNATURE_IMAGE_CID}>"


def test_the_part_is_inline_not_an_attachment():
    part = signature_image_part(PNG_B64, "image/png")
    assert part.get("Content-Disposition", "").startswith("inline")


def test_the_part_declares_the_sniffed_type():
    assert signature_image_part(JPEG_B64, "image/jpeg").get_content_type() == "image/jpeg"


def test_the_part_holds_the_original_bytes():
    part = signature_image_part(PNG_B64, "image/png")
    assert part.get_payload(decode=True) == base64.b64decode(PNG_B64)


# ── Withholding it ────────────────────────────────────────────────

def test_the_image_rides_on_the_signature_text():
    """The composer puts the text in the draft so the user can delete it to
    send one unsigned message; the logo must not go out regardless."""
    signed = apply_signature("Hi there", SIG)
    assert body_has_signature(signed, SIG) is True
    assert body_has_signature("Hi there", SIG) is False
