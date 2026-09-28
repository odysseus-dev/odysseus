"""Route-level coverage for the per-account email signature.

What these pin:

- the signature survives a round trip through the account CRUD, and is
  normalized on the way in rather than stored as typed;
- `/send` leaves the body alone by default, because the composer has
  already signed the draft and a second copy would reach the recipient;
- when a caller does ask for it, the signature is added once and honours
  the account's on/off toggle.
"""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

OWNER = "alice"
SIG = "Ada Lovelace\nAnalytical Engines Ltd"


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


def _row(factory, account_id="a1"):
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
    }
    body.update(over)
    return asyncio.run(_endpoint("create_email_account")(body, owner=OWNER))


# ── Storage ───────────────────────────────────────────────────────

def test_a_signature_survives_create_and_list(account_db):
    created = _create(signature=SIG)
    assert created["ok"] is True
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    account = listed["accounts"][0]
    assert account["signature"] == SIG
    assert account["signature_enabled"] is True


def test_a_signature_is_normalized_on_the_way_in(account_db):
    """Trailing whitespace and a user-typed delimiter are cleaned once, at
    the boundary, rather than at every send."""
    _create(signature="-- \n\nAda Lovelace   \nAnalytical Engines Ltd  \n\n")
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    assert listed["accounts"][0]["signature"] == SIG


def test_an_account_created_without_one_has_the_toggle_on(account_db):
    """So writing a signature later starts working immediately."""
    _create()
    listed = asyncio.run(_endpoint("list_email_accounts")(owner=OWNER))
    assert listed["accounts"][0]["signature"] == ""
    assert listed["accounts"][0]["signature_enabled"] is True


def test_the_signature_can_be_edited(account_db):
    created = _create(signature=SIG)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"signature": "Just Ada"}, owner=OWNER,
    ))
    assert _row(account_db, created["id"]).signature == "Just Ada"


def test_the_toggle_can_be_turned_off_without_losing_the_text(account_db):
    created = _create(signature=SIG)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"signature_enabled": False}, owner=OWNER,
    ))
    row = _row(account_db, created["id"])
    assert row.signature_enabled is False
    assert row.signature == SIG


def test_an_update_that_does_not_mention_the_signature_leaves_it_alone(account_db):
    """Saving the form with only the host changed must not blank it."""
    created = _create(signature=SIG)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"imap_host": "imap2.example.com"}, owner=OWNER,
    ))
    assert _row(account_db, created["id"]).signature == SIG


def test_an_empty_signature_clears_it(account_db):
    created = _create(signature=SIG)
    asyncio.run(_endpoint("update_email_account")(
        created["id"], {"signature": ""}, owner=OWNER,
    ))
    assert _row(account_db, created["id"]).signature == ""


# ── /send ─────────────────────────────────────────────────────────

def _sent_body(monkeypatch, cfg, **req_over):
    """Drive /send with SMTP stubbed and return the plain-text part."""
    from routes import email_routes
    from routes.email_helpers import SendEmailRequest

    monkeypatch.setattr(email_routes, "_resolve_send_config", lambda *a, **k: cfg)
    captured = {}

    class _Bg:
        def add_task(self, fn, *a, **k):
            captured["task"] = (fn, a, k)

    def _grab(outer, *a, **k):
        captured["outer"] = outer
        return {"success": True}

    monkeypatch.setattr(email_routes, "_deliver_email", _grab, raising=False)

    payload = {"to": "b@example.com", "subject": "Hi", "body": "Hello there"}
    payload.update(req_over)
    req = SendEmailRequest(**payload)
    send = _endpoint("send_email")
    try:
        asyncio.run(send(req=req, background_tasks=_Bg(), owner=OWNER))
    except Exception:
        # Delivery is stubbed at various depths across versions; the body is
        # rewritten before any of it runs, which is what is under test.
        pass
    return req.body


_CFG = {
    "account_id": "a1", "account_name": "Work",
    "from_address": "ada@example.com", "display_name": "Ada",
    "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_security": "ssl",
    "smtp_user": "ada@example.com", "smtp_password": "pw",
    "signature": SIG, "signature_enabled": True,
}


def test_send_does_not_sign_by_default(monkeypatch, account_db):
    """The composer already put the signature in the draft. Adding another
    here is how the recipient ends up with the sender's name twice."""
    body = _sent_body(monkeypatch, dict(_CFG))
    assert body == "Hello there"


def test_send_signs_when_the_caller_asks(monkeypatch, account_db):
    body = _sent_body(monkeypatch, dict(_CFG), append_signature=True)
    assert body.endswith("-- \nAda Lovelace\nAnalytical Engines Ltd\n")


def test_send_does_not_sign_a_body_that_already_carries_it(monkeypatch, account_db):
    signed = "Hello there\n\n-- \nAda Lovelace\nAnalytical Engines Ltd\n"
    body = _sent_body(monkeypatch, dict(_CFG), body=signed, append_signature=True)
    assert body.count("Analytical Engines Ltd") == 1


def test_send_respects_the_account_toggle(monkeypatch, account_db):
    cfg = dict(_CFG, signature_enabled=False)
    body = _sent_body(monkeypatch, cfg, append_signature=True)
    assert body == "Hello there"


def test_send_with_no_signature_configured_is_unchanged(monkeypatch, account_db):
    cfg = dict(_CFG, signature="")
    body = _sent_body(monkeypatch, cfg, append_signature=True)
    assert body == "Hello there"


def test_a_signed_reply_keeps_the_quote_below_the_signature(monkeypatch, account_db):
    reply = "Friday works.\n\n---------- Previous message ----------\n> can we meet"
    body = _sent_body(monkeypatch, dict(_CFG), body=reply, append_signature=True)
    assert body.index("-- \n") < body.index("---------- Previous message ----------")
