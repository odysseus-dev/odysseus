import email as email_mod
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus-email-m1-"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes import email_helpers as eh
from routes.email_helpers import (
    build_message_source,
    decrypt_cache_field,
    email_ai_local_only_error,
    encrypt_cache_field,
    group_emails_by_thread,
    upsert_email_snooze,
    sweep_expired_snoozes,
    _active_snoozed_uids,
)


def test_encrypt_cache_field_round_trip():
    plain = "secret summary bullet one"
    enc = encrypt_cache_field(plain)
    assert enc != plain
    assert enc.startswith("enc:")
    assert decrypt_cache_field(enc) == plain


def test_decrypt_cache_field_legacy_plaintext():
    assert decrypt_cache_field("still plain") == "still plain"


def test_email_ai_local_only_blocks_remote(monkeypatch):
    monkeypatch.setattr(eh, "_load_settings", lambda: {"email_ai_local_only": True})
    assert email_ai_local_only_error("https://api.openai.com/v1") is not None
    assert email_ai_local_only_error("http://127.0.0.1:11434/v1") is None
    monkeypatch.setattr(eh, "_load_settings", lambda: {"email_ai_local_only": False})
    assert email_ai_local_only_error("https://api.openai.com/v1") is None


def test_build_message_source_multipart():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Hello\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="b"\r\n'
        b"\r\n"
        b"--b\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body\r\n"
        b"--b\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>HTML body</p>\r\n"
        b"--b--\r\n"
    )
    msg = email_mod.message_from_bytes(raw)
    payload = build_message_source(msg, raw)
    assert payload["size"] == len(raw)
    assert any(h["name"] == "Subject" for h in payload["headers"])
    bodies = [p.get("body", "") for p in payload["parts"] if p.get("body")]
    assert "Plain body" in bodies[0]
    assert "<p>HTML body</p>" in bodies[1]
    assert payload["raw_rfc822"]


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.asyncio
async def test_flag_and_unflag_endpoints(monkeypatch):
    import routes.email_routes as email_routes

    calls = []

    class _Conn:
        def select(self, *_a, **_k):
            return None

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *_a):
            return False

    def fake_store(conn, uid, flag, add=True):
        calls.append((uid, flag, add))
        return True

    monkeypatch.setattr(email_routes, "_imap", lambda *a, **k: _Ctx())
    monkeypatch.setattr(email_routes, "_store_email_flag", fake_store)

    router = email_routes.setup_email_routes()
    flag_ep = _route_endpoint(router, "/api/email/flag/{uid}", "POST")
    unflag_ep = _route_endpoint(router, "/api/email/unflag/{uid}", "POST")

    r1 = await flag_ep("42", folder="INBOX", account_id=None, owner="alice")
    r2 = await unflag_ep("42", folder="INBOX", account_id=None, owner="alice")

    assert r1["success"] is True and r1["is_flagged"] is True
    assert r2["success"] is True and r2["is_flagged"] is False
    assert calls == [("42", "\\Flagged", True), ("42", "\\Flagged", False)]


@pytest.mark.asyncio
async def test_message_source_endpoint(monkeypatch):
    import routes.email_routes as email_routes

    raw = (
        b"From: a@example.com\r\nSubject: T\r\n\r\n"
        b"Hello world\r\n"
    )
    msg = email_mod.message_from_bytes(raw)

    class _Conn:
        def select(self, *_a, **_k):
            return None

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(email_routes, "_imap", lambda *a, **k: _Ctx())
    monkeypatch.setattr(
        email_routes,
        "_imap_uid_fetch",
        lambda conn, uid, spec: ("OK", [(b"1 (BODY[] {%d})" % len(raw), raw)]),
    )

    router = email_routes.setup_email_routes()
    ep = _route_endpoint(router, "/api/email/source/{uid}", "GET")
    out = await ep("1", folder="INBOX", account_id=None, owner="alice")
    assert out["uid"] == "1"
    assert out["headers"]
    assert out["raw_rfc822"]


def test_eml_download_disposition_ascii_safe():
    from routes.email_routes import _eml_download_disposition

    disp = _eml_download_disposition("Sąskaita faktūra Nr. TEL 05591", "28082")
    assert 'filename="' in disp
    assert "filename*=UTF-8" in disp
    # Must not contain raw Lithuanian in the quoted filename (latin-1 header safe).
    assert "ą" not in disp.split('filename="')[1].split('"')[0]


def test_group_emails_by_thread():
    emails = [
        {"uid": "2", "message_id": "<b@x>", "in_reply_to": "<a@x>", "references": "<a@x>", "date_epoch": 200, "subject": "Re: hi"},
        {"uid": "1", "message_id": "<a@x>", "in_reply_to": "", "references": "", "date_epoch": 100, "subject": "hi"},
    ]
    grouped = group_emails_by_thread(emails)
    assert len(grouped) == 1
    assert grouped[0]["thread_count"] == 2
    assert grouped[0]["uid"] == "2"


def test_snooze_hides_uid_until_wake(monkeypatch, tmp_path):
    db = tmp_path / "scheduled.db"
    monkeypatch.setattr(eh, "SCHEDULED_DB", str(db))
    eh._init_scheduled_db()
    from datetime import datetime as _dt, timedelta as _td
    wake = (_dt.utcnow() + _td(hours=2)).isoformat()
    upsert_email_snooze(
        message_id="<m@x>", owner="alice", account_id="acc1",
        uid="99", folder="INBOX", wake_at=wake,
    )
    hidden = _active_snoozed_uids("alice", "acc1", "INBOX")
    assert "99" in hidden
    past = (_dt.utcnow() - _td(minutes=1)).isoformat()
    upsert_email_snooze(
        message_id="<old@x>", owner="alice", account_id="acc1",
        uid="88", folder="INBOX", wake_at=past,
    )
    sweep_expired_snoozes()
    hidden2 = _active_snoozed_uids("alice", "acc1", "INBOX")
    assert "88" not in hidden2
    assert "99" in hidden2
