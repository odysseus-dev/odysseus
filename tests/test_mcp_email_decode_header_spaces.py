"""mcp email server _decode_header must not inject spaces between parts.

email.header.decode_header returns plain-text runs WITH their surrounding
whitespace (e.g. (b"Re: ", None)), so joining parts with " " produced a
double space after "Re:" on every non-ASCII subject, a spurious space in
"Name <addr>" senders, and violated RFC 2047 6.2 which requires whitespace
between two adjacent encoded-words to be dropped.
"""
import json
import sqlite3

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


def _init_accounts_db(db_path, monkeypatch):
    """Seed email_accounts and point the MCP account reader at the fixture DB.

    _read_accounts_from_db() now reads via core.database.SessionLocal (so it
    follows DATABASE_URL, incl. Postgres), so the test routes THAT — not the old
    raw-sqlite3 ``APP_DB`` path — at a temp database seeded through the ORM.
    """
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import core.database as db_mod

    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    db_mod.EmailAccount.__table__.create(eng, checkfirst=True)
    Make = sessionmaker(bind=eng)
    s = Make()
    try:
        s.add_all([
            db_mod.EmailAccount(
                id="acct-alice", owner="alice", name="Alice Mail",
                is_default=True, enabled=True,
                imap_host="imap.example.com", imap_port=993,
                imap_user="alice@example.com", imap_password="", imap_starttls=True,
                smtp_host="smtp.example.com", smtp_port=465, smtp_security="ssl",
                smtp_user="alice@example.com", smtp_password="",
                from_address="alice@example.com", created_at=datetime(2026, 1, 1),
            ),
            db_mod.EmailAccount(
                id="acct-bob", owner="bob", name="Bob Mail",
                is_default=True, enabled=True,
                imap_host="imap.example.com", imap_port=993,
                imap_user="bob@example.com", imap_password="", imap_starttls=True,
                smtp_host="smtp.example.com", smtp_port=465, smtp_security="ssl",
                smtp_user="bob@example.com", smtp_password="",
                from_address="bob@example.com", created_at=datetime(2026, 1, 2),
            ),
        ])
        s.commit()
    finally:
        s.close()
    monkeypatch.setattr(db_mod, "SessionLocal", Make)


def test_prefix_then_encoded_word_single_space():
    assert es._decode_header("Re: =?utf-8?b?SsOzc2U=?=") == "Re: J\u00f3se"


def test_encoded_word_then_plain_text():
    assert es._decode_header("=?utf-8?b?SsOzc2U=?= Smith") == "J\u00f3se Smith"


def test_adjacent_encoded_words_join_without_space():
    out = es._decode_header("=?iso-8859-1?q?Caf=E9?= =?utf-8?b?5pel5pys?=")
    assert out == "Caf\u00e9\u65e5\u672c"


def test_plain_ascii_header_unchanged():
    assert es._decode_header("Weekly report") == "Weekly report"


def test_empty_header():
    assert es._decode_header("") == ""


@pytest.mark.asyncio
async def test_mcp_email_accounts_are_filtered_by_hidden_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(db_path, monkeypatch)
    es._ACCOUNT_CACHE.clear()

    out = await es.call_tool("list_email_accounts", {"_odysseus_owner": "alice"})
    text = out[0].text

    assert "Alice Mail" in text
    assert "Bob Mail" not in text


@pytest.mark.asyncio
async def test_mcp_email_requires_owner_when_multiple_account_owners_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    _init_accounts_db(db_path, monkeypatch)
    es._ACCOUNT_CACHE.clear()

    out = await es.call_tool("list_email_accounts", {})

    assert "requires an authenticated owner" in out[0].text


def test_mcp_email_scoped_owner_without_visible_account_skips_legacy_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    settings_path = tmp_path / "settings.json"
    _init_accounts_db(db_path, monkeypatch)
    settings_path.write_text(
        json.dumps(
            {
                "imap_host": "legacy-imap.example.com",
                "imap_user": "legacy@example.com",
                "imap_password": "legacy-secret",
                "smtp_host": "legacy-smtp.example.com",
                "smtp_user": "legacy@example.com",
                "smtp_password": "legacy-secret",
                "from_address": "legacy@example.com",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(es, "_SETTINGS_FILE", str(settings_path))
    es._ACCOUNT_CACHE.clear()

    token = es._CURRENT_OWNER.set("charlie")
    try:
        with pytest.raises(ValueError, match="No email account is configured"):
            es._load_config()
    finally:
        es._CURRENT_OWNER.reset(token)
        es._ACCOUNT_CACHE.clear()


@pytest.mark.asyncio
async def test_mcp_send_email_stages_owner_scoped_pending_draft(tmp_path, monkeypatch):
    import src.constants as constants

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(constants, "SCHEDULED_EMAILS_DB", str(db_path))
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: True)

    out = await es.call_tool(
        "send_email",
        {
            "to": "recipient@example.com",
            "subject": "Review",
            "body": "Please review.",
            "_odysseus_owner": "alice",
        },
    )

    assert "Draft staged for approval" in out[0].text
    assert "Nothing has been sent yet" in out[0].text
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner, status, to_addr, subject FROM scheduled_emails"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("alice", "agent_draft", "recipient@example.com", "Review")


@pytest.mark.asyncio
async def test_mcp_draft_email_document_uses_hidden_owner(monkeypatch):
    import core.database as db_mod

    saved = []

    class FakeDocument:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDocumentVersion:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDb:
        def add(self, obj):
            saved.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(db_mod, "Document", FakeDocument)
    monkeypatch.setattr(db_mod, "DocumentVersion", FakeDocumentVersion)
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(
        es,
        "_load_config",
        lambda account=None: {"account_name": "Alice Mail", "account_id": "acct-alice"},
    )

    out = await es.call_tool(
        "draft_email",
        {
            "to": "recipient@example.com",
            "subject": "Draft subject",
            "body": "Draft body",
            "_odysseus_owner": "alice",
        },
    )

    assert "Created Odysseus email draft" in out[0].text
    docs = [obj for obj in saved if isinstance(obj, FakeDocument)]
    assert len(docs) == 1
    assert docs[0].owner == "alice"
