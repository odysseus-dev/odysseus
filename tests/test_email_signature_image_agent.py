"""The agent's mail carries the signature image the same way.

The agent path builds an `EmailMessage` and sends text/plain, so an image
needs an HTML alternative to reference. What these pin:

- the nesting is the one clients render, not a loose attachment;
- the plain part is untouched, so a text-only reader still gets the body;
- it is best-effort — decoration must never cost a send that is otherwise
  ready to go out.
"""

import base64
import email as email_mod
import struct
import zlib
from email.message import EmailMessage

import pytest

from src.email_signature import SIGNATURE_IMAGE_CID, account_signature, apply_signature

SIG = "Ada Lovelace\nAnalytical Engines Ltd"


def _png():
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


PNG = _png()
PNG_B64 = base64.b64encode(PNG).decode()


@pytest.fixture(scope="module")
def attach():
    """Lift the helper out of the MCP server without importing the module.

    Importing it pulls in the whole mail tool surface; the function under
    test is self-contained and does its own lazy imports.
    """
    src = open("mcp_servers/email_server.py", encoding="utf-8").read()
    start = src.index("def _attach_signature_image(")
    end = src.index("def _signed_body(")
    namespace = {}
    exec(compile(src[start:end], "email_server_fragment", "exec"), namespace)
    return namespace["_attach_signature_image"]


def _cfg(**over):
    cfg = {
        "signature": SIG,
        "signature_enabled": True,
        "signature_image": PNG_B64,
        "signature_image_mime": "image/png",
    }
    cfg.update(over)
    return cfg


def _message(body):
    msg = EmailMessage()
    msg["Subject"] = "Follow-up"
    msg["From"] = "ada@example.com"
    msg["To"] = "b@example.com"
    msg.set_content(body)
    return msg


def _signed(text="As agreed, sending this over."):
    return apply_signature(text, account_signature(_cfg()))


def _types(msg):
    return [p.get_content_type() for p in msg.walk()]


def test_the_image_is_related_to_the_html_that_references_it(attach):
    body = _signed()
    msg = _message(body)
    assert attach(msg, body, _cfg()) is True
    assert _types(msg) == [
        "multipart/alternative",
        "text/plain",
        "multipart/related",
        "text/html",
        "image/png",
    ]


def test_the_html_references_the_content_id(attach):
    body = _signed()
    msg = _message(body)
    attach(msg, body, _cfg())
    parsed = email_mod.message_from_bytes(msg.as_bytes())
    html = [p for p in parsed.walk() if p.get_content_type() == "text/html"][0]
    image = [p for p in parsed.walk() if p.get_content_maintype() == "image"][0]
    assert f"cid:{SIGNATURE_IMAGE_CID}" in html.get_payload(decode=True).decode()
    assert image.get("Content-ID") == f"<{SIGNATURE_IMAGE_CID}>"
    assert image.get_payload(decode=True) == PNG


def test_the_image_part_is_inline(attach):
    body = _signed()
    msg = _message(body)
    attach(msg, body, _cfg())
    parsed = email_mod.message_from_bytes(msg.as_bytes())
    image = [p for p in parsed.walk() if p.get_content_maintype() == "image"][0]
    assert image.get("Content-Disposition", "").startswith("inline")


def test_the_plain_part_still_carries_the_body_and_the_text_signature(attach):
    body = _signed()
    msg = _message(body)
    attach(msg, body, _cfg())
    parsed = email_mod.message_from_bytes(msg.as_bytes())
    plain = [p for p in parsed.walk() if p.get_content_type() == "text/plain"][0]
    text = plain.get_payload(decode=True).decode()
    assert "As agreed, sending this over." in text
    assert text.rstrip().endswith("Analytical Engines Ltd")
    assert "cid:" not in text


def test_a_body_the_user_unsigned_gets_no_image(attach):
    """The user approves the agent's draft before it goes out; removing the
    signature there has to remove the logo with it."""
    body = "As agreed, sending this over."
    msg = _message(body)
    assert attach(msg, body, _cfg()) is False
    assert _types(msg) == ["text/plain"]


def test_an_account_with_no_image_is_left_alone(attach):
    body = _signed()
    msg = _message(body)
    assert attach(msg, body, _cfg(signature_image="")) is False
    assert _types(msg) == ["text/plain"]


def test_the_switch_withholds_the_image(attach):
    body = _signed()
    msg = _message(body)
    assert attach(msg, body, _cfg(signature_enabled=False)) is False
    assert _types(msg) == ["text/plain"]


@pytest.mark.parametrize("broken", ["not base64", "", None])
def test_an_unusable_stored_image_costs_the_logo_not_the_send(attach, broken):
    body = _signed()
    msg = _message(body)
    assert attach(msg, body, _cfg(signature_image=broken)) is False
    assert _types(msg) == ["text/plain"]


def test_a_config_missing_the_keys_entirely_is_survivable(attach):
    """Rows read from an older database have no image columns at all."""
    body = _signed()
    msg = _message(body)
    assert attach(msg, body, {"signature": SIG, "signature_enabled": True}) is False
    assert _types(msg) == ["text/plain"]
