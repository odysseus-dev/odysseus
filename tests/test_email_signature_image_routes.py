"""The signature image through the account routes and the assembled message.

What these pin:

- the account list advertises the image without carrying its bytes, so the
  settings page does not download megabytes of base64 to draw a thumbnail;
- an update that does not mention the image leaves it alone, and an empty
  string is what removes it;
- the message that actually goes to SMTP nests the parts so the logo
  renders where the body references it instead of arriving as a download.
"""

import asyncio
import base64
import email as email_mod
import struct
import zlib
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

OWNER = "alice"
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
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"


@pytest.fixture
def account_db(tmp_path, monkeypatch):
    from core import database as core_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    core_db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _endpoint(name):
    from routes import email_routes

    with mock.patch.object(email_routes, "_start_poller"):
        router = email_routes.setup_email_routes()
    for route in router.routes:
        if getattr(getattr(route, "endpoint", None), "__name__", "") == name:
            return route.endpoint
    raise AssertionError(f"email route not found: {name}")


def _row(factory, account_id):
    from core.database import EmailAccount

    db = factory()
    try:
        return db.get(EmailAccount, account_id)
    finally:
        db.close()


def _create(**over):
    body = {
        "name": "Work",
        "from_address": "ada@example.com",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "smtp_user": "ada@example.com",
        "signature": SIG,
    }
    body.update(over)
    return asyncio.run(_endpoint("create_email_account")(body, owner=OWNER))


# ── Storage ───────────────────────────────────────────────────────

def test_an_image_survives_create(account_db):
    created = _create(signature_image=PNG_DATA_URL)
    assert created["ok"] is True
    row = _row(account_db, created["id"])
    assert base64.b64decode(row.signature_image) == PNG
    assert row.signature_image_mime == "image/png"


def test_the_list_advertises_the_image_without_shipping_it(account_db):
    """A few accounts with a logo each would otherwise make the settings
    page download a megabyte of base64 to draw thumbnails."""
    _create(signature_image=PNG_DATA_URL)
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    account = listed["accounts"][0]
    assert account["has_signature_image"] is True
    assert account["signature_image_mime"] == "image/png"
    assert "signature_image" not in account


def test_an_account_without_an_image_says_so(account_db):
    _create()
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    assert listed["accounts"][0]["has_signature_image"] is False


def test_a_rejected_image_does_not_create_the_account(account_db):
    bad = base64.b64encode(b"MZ\x90\x00 executable").decode()
    result = _create(signature_image=bad)
    assert result["ok"] is False
    assert "PNG, JPEG or GIF" in result["error"]
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    assert listed["accounts"] == []


def test_an_image_can_be_added_to_an_existing_account(account_db):
    created = _create()
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"signature_image": PNG_DATA_URL}, owner=OWNER,
    ))
    assert _row(account_db, created["id"]).signature_image == PNG_B64


def test_an_update_that_does_not_mention_the_image_leaves_it_alone(account_db):
    """The form does not re-upload the image on every save, so an absent
    key has to mean "unchanged" rather than "remove it"."""
    created = _create(signature_image=PNG_DATA_URL)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"imap_host": "imap2.example.com"}, owner=OWNER,
    ))
    assert _row(account_db, created["id"]).signature_image == PNG_B64


def test_an_empty_string_removes_the_image(account_db):
    created = _create(signature_image=PNG_DATA_URL)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"signature_image": ""}, owner=OWNER,
    ))
    row = _row(account_db, created["id"])
    assert row.signature_image is None
    assert row.signature_image_mime is None


def test_a_rejected_update_leaves_the_stored_image_intact(account_db):
    created = _create(signature_image=PNG_DATA_URL)
    result = asyncio.run(_endpoint("update_email_account")(
        created["id"],
        {"signature_image": base64.b64encode(b"MZ\x90\x00 nope").decode()},
        owner=OWNER,
    ))
    assert result["ok"] is False
    assert _row(account_db, created["id"]).signature_image == PNG_B64


# ── The preview endpoint ──────────────────────────────────────────

def test_the_preview_serves_the_stored_bytes(account_db, monkeypatch):
    from routes import email_routes

    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda *a, **k: None)
    created = _create(signature_image=PNG_DATA_URL)
    resp = asyncio.run(_endpoint("get_signature_image")(created["id"], owner=OWNER))
    assert resp.body == PNG
    assert resp.media_type == "image/png"


def test_the_preview_shows_the_image_even_when_sending_is_switched_off(
    account_db, monkeypatch
):
    """The switch governs sending. Hiding the stored image behind it would
    make the settings page look like the upload had failed."""
    from routes import email_routes

    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda *a, **k: None)
    created = _create(signature_image=PNG_DATA_URL, signature_enabled=False)
    resp = asyncio.run(_endpoint("get_signature_image")(created["id"], owner=OWNER))
    assert resp.body == PNG


def test_the_preview_is_404_when_there_is_no_image(account_db, monkeypatch):
    from fastapi import HTTPException
    from routes import email_routes

    monkeypatch.setattr(email_routes, "_assert_owns_account", lambda *a, **k: None)
    created = _create()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_endpoint("get_signature_image")(created["id"], owner=OWNER))
    assert excinfo.value.status_code == 404


# ── The assembled message ─────────────────────────────────────────

_CFG = {
    "account_id": "a1", "account_name": "Work",
    "from_address": "ada@example.com", "display_name": "Ada",
    "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_security": "ssl",
    "smtp_user": "ada@example.com", "smtp_password": "pw",
    "signature": SIG, "signature_enabled": True,
    "signature_image": PNG_B64, "signature_image_mime": "image/png",
}


def _sent_message(monkeypatch, cfg, **req_over):
    """Drive /send with SMTP stubbed and return the parsed outgoing message."""
    from routes import email_routes
    from routes.email_helpers import SendEmailRequest

    monkeypatch.setattr(email_routes, "_resolve_send_config", lambda *a, **k: cfg)
    captured = {}

    class _Bg:
        def add_task(self, fn, *a, **k):
            captured["task"] = (fn, a, k)

    def _grab(_smtp_cfg, _from, _rcpt, raw):
        captured["raw"] = raw

    monkeypatch.setattr(email_routes, "_send_smtp_message", _grab, raising=False)
    monkeypatch.setattr(email_routes, "_imap", mock.MagicMock(), raising=False)

    payload = {"to": "b@example.com", "subject": "Hi", "body": "Hello there"}
    payload.update(req_over)
    req = SendEmailRequest(**payload)
    asyncio.run(_endpoint("send_email")(req=req, background_tasks=_Bg(), owner=OWNER))
    fn, args, kwargs = captured["task"]
    try:
        fn(*args, **kwargs)
    except Exception:
        # Everything after the SMTP hand-off (IMAP append, flag updates) is
        # not stubbed; the bytes were captured before any of it runs.
        pass
    return email_mod.message_from_string(captured["raw"])


def _types(msg):
    return [p.get_content_type() for p in msg.walk()]


def _signed(body=None):
    from src.email_signature import apply_signature

    return apply_signature(body or "Hello there", SIG)


def test_the_image_is_related_to_the_body_not_a_sibling(monkeypatch):
    """A part with no stated relation to the HTML is what makes clients
    list the logo as a download instead of rendering it in place."""
    msg = _sent_message(monkeypatch, _CFG, body=_signed())
    assert msg.get_content_type() == "multipart/related"
    assert _types(msg) == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/png",
    ]


def test_the_html_part_references_the_image_by_content_id(monkeypatch):
    from src.email_signature import SIGNATURE_IMAGE_CID

    msg = _sent_message(monkeypatch, _CFG, body=_signed())
    html = [p for p in msg.walk() if p.get_content_type() == "text/html"][0]
    image = [p for p in msg.walk() if p.get_content_maintype() == "image"][0]
    assert f"cid:{SIGNATURE_IMAGE_CID}" in html.get_payload(decode=True).decode()
    assert image.get("Content-ID") == f"<{SIGNATURE_IMAGE_CID}>"
    assert image.get_payload(decode=True) == PNG


def test_the_plain_part_carries_the_text_signature_and_no_markup(monkeypatch):
    """text/plain cannot show a picture, so the text stays the part that
    always arrives."""
    msg = _sent_message(monkeypatch, _CFG, body=_signed())
    plain = [p for p in msg.walk() if p.get_content_type() == "text/plain"][0]
    text = plain.get_payload(decode=True).decode()
    assert text.rstrip().endswith("Analytical Engines Ltd")
    assert "cid:" not in text
    assert "<img" not in text


def test_the_headers_live_on_the_part_that_is_sent(monkeypatch):
    """The related wrapper is the outermost part now, so the headers have
    to be set on it rather than on the alternative it replaced."""
    msg = _sent_message(monkeypatch, _CFG, body=_signed())
    assert msg["Subject"] == "Hi"
    assert msg["To"] == "b@example.com"
    assert msg["Message-ID"]
    assert msg["Date"]


def test_a_reply_keeps_its_threading_headers(monkeypatch):
    msg = _sent_message(
        monkeypatch, _CFG, body=_signed(),
        in_reply_to="<prev@example.com>", references="<prev@example.com>",
    )
    assert msg["In-Reply-To"] == "<prev@example.com>"
    assert msg["References"] == "<prev@example.com>"


def test_an_unsigned_draft_sends_without_the_image(monkeypatch):
    """Deleting the signature from the draft is how the user sends one
    unsigned message; the logo must not go out regardless."""
    msg = _sent_message(monkeypatch, _CFG, body="Just the text, no signature")
    assert msg.get_content_type() == "multipart/alternative"
    assert "image/png" not in _types(msg)


def test_the_switch_withholds_the_image(monkeypatch):
    cfg = dict(_CFG, signature_enabled=False)
    msg = _sent_message(monkeypatch, cfg, body=_signed())
    assert "image/png" not in _types(msg)


def test_an_account_without_an_image_sends_the_usual_shape(monkeypatch):
    cfg = dict(_CFG, signature_image="", signature_image_mime="")
    msg = _sent_message(monkeypatch, cfg, body=_signed())
    assert msg.get_content_type() == "multipart/alternative"
    assert _types(msg) == ["multipart/alternative", "text/plain", "text/html"]


def test_an_unreadable_stored_image_does_not_break_the_send(monkeypatch):
    """A column holding something unusable must cost the logo, not the mail."""
    cfg = dict(_CFG, signature_image="not base64 at all")
    msg = _sent_message(monkeypatch, cfg, body=_signed())
    assert msg.get_content_type() == "multipart/alternative"
    plain = [p for p in msg.walk() if p.get_content_type() == "text/plain"][0]
    assert "Hello there" in plain.get_payload(decode=True).decode()
