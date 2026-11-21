"""Isolated unit and WAL/read-only verification tests for local_intelligence_gateway.py (CP-3B)."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.local_intelligence_gateway import (
    AuthContext,
    fuse_rrf,
    resolve_auth_from_session,
    resolve_auth_from_token,
    ro_connect,
    sanitize_snippet,
    search_local_intelligence,
)


@pytest.fixture()
def isolated_env(tmp_path: Path) -> dict[str, Path]:
    """Create isolated WAL-mode SQLite databases with test fixtures (zero live DB touch)."""
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(minutes=15)).isoformat()
    expired_ts = (now - timedelta(hours=72)).isoformat()

    # 1. Isolated app.db
    app_db = tmp_path / "app.db"
    con = sqlite3.connect(str(app_db))
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            owner TEXT,
            archived INTEGER DEFAULT 0,
            updated_at TEXT,
            created_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE api_tokens (
            id TEXT PRIMARY KEY,
            owner TEXT,
            name TEXT,
            token_hash TEXT,
            token_prefix TEXT,
            scopes TEXT,
            is_active INTEGER DEFAULT 1
        )
        """
    )
    con.execute(
        "INSERT INTO sessions VALUES (?, ?, 0, ?, ?)",
        ("sess_active_user1", "alice", recent_ts, recent_ts),
    )
    con.execute(
        "INSERT INTO sessions VALUES (?, ?, 0, ?, ?)",
        ("sess_expired_user1", "alice", expired_ts, expired_ts),
    )
    con.execute(
        "INSERT INTO sessions VALUES (?, ?, 1, ?, ?)",
        ("sess_archived_user1", "alice", recent_ts, recent_ts),
    )

    tok_todos = "ody_todos_only_secret_123"
    tok_docs = "ody_docs_media_secret_456"
    tok_revoked = "ody_revoked_secret_789"
    con.execute(
        "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, ?, 1)",
        ("t1", "alice", "Monitor", hashlib.sha256(tok_todos.encode()).hexdigest(), tok_todos[:8], "todos:read"),
    )
    con.execute(
        "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, ?, 1)",
        ("t2", "alice", "Reader", hashlib.sha256(tok_docs.encode()).hexdigest(), tok_docs[:8], "docs:read,media:read,personal:read,business:read,tier:restricted-financial"),
    )
    con.execute(
        "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, ?, 0)",
        ("t3", "alice", "Revoked", hashlib.sha256(tok_revoked.encode()).hexdigest(), tok_revoked[:8], "docs:read,media:read,personal:read"),
    )
    con.commit()
    con.close()

    # 2. Isolated private_ocr_index.db
    ocr_db = tmp_path / "private_ocr_index.db"
    con = sqlite3.connect(str(ocr_db))
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute(
        """
        CREATE TABLE ocr_documents (
            sha256 TEXT PRIMARY KEY,
            source_path TEXT,
            filename TEXT,
            sensitivity_tier TEXT,
            category TEXT,
            owner_scope TEXT,
            status TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE ocr_pages (
            id INTEGER PRIMARY KEY,
            sha256 TEXT,
            page_num INTEGER,
            extracted_text TEXT
        )
        """
    )
    con.execute(
        """
        CREATE VIRTUAL TABLE ocr_pages_fts USING fts5(
            sha256, filename, sensitivity_tier, category, page_num, extracted_text
        )
        """
    )
    docs = [
        (
            "hash_bank_1",
            "G:/Ody_AI Organized/01 - Banking/stmt_2026.pdf",
            "stmt_2026.pdf",
            "restricted-financial",
            "Banking",
            "personal",
            "completed",
            1,
            "Wells Fargo Statement dated 06/15/2026 balance $1,250.00 Account Number: 9876543210 podcast gear",
        ),
        (
            "hash_pii_1",
            "G:/Ody_AI Organized/05 - Housing/storage_info.pdf",
            "storage_info.pdf",
            "restricted-pii",
            "Storage",
            "personal",
            "completed",
            1,
            "Public Storage Space 0551 Gate Code: *451963# podcast equipment box",
        ),
        (
            "hash_biz_1",
            "G:/Ody_AI Organized/02 - Business/invoice_2026.pdf",
            "invoice_2026.pdf",
            "business-rag",
            "Invoices",
            "business",
            "completed",
            2,
            "Client podcast editing invoice total $450.00 paid on 2026-09-01",
        ),
    ]
    for sha, sp, fn, tier, cat, oscope, st, pnum, txt in docs:
        con.execute("INSERT INTO ocr_documents VALUES (?, ?, ?, ?, ?, ?, ?)", (sha, sp, fn, tier, cat, oscope, st))
        con.execute("INSERT INTO ocr_pages (sha256, page_num, extracted_text) VALUES (?, ?, ?)", (sha, pnum, txt))
        con.execute("INSERT INTO ocr_pages_fts VALUES (?, ?, ?, ?, ?, ?)", (sha, fn, tier, cat, str(pnum), txt))
    con.commit()
    con.close()

    # 3. Isolated stream_media_catalog.db
    media_db = tmp_path / "stream_media_catalog.db"
    con = sqlite3.connect(str(media_db))
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute(
        """
        CREATE TABLE media_items (
            id INTEGER PRIMARY KEY,
            scope TEXT,
            source_path TEXT,
            filename TEXT,
            media_role TEXT,
            duration_sec REAL,
            is_duplicate INTEGER,
            transcript_status TEXT,
            transcript_srt_path TEXT,
            transcript_json_path TEXT
        )
        """
    )
    con.execute(
        """
        CREATE VIRTUAL TABLE transcript_segments_fts USING fts5(
            media_id, filename, start_sec, end_sec, segment_text
        )
        """
    )
    con.execute(
        "INSERT INTO media_items VALUES (101, 'd_creator', 'D:/Creator/clip1.mp4', 'clip1.mp4', 'creator_video_clip', 42.5, 0, 'completed', 'data/stream_transcripts/media_101/clip1.srt', 'data/stream_transcripts/media_101/clip1.json')"
    )
    con.execute(
        "INSERT INTO transcript_segments_fts VALUES ('101', 'clip1.mp4', '12.5', '18.0', 'Setting up the new podcast microphone on stream today')"
    )
    con.commit()
    con.close()

    # 4. Isolated drive_inventory.db
    inv_db = tmp_path / "drive_inventory.db"
    con = sqlite3.connect(str(inv_db))
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute(
        """
        CREATE TABLE inventory_files (
            id INTEGER PRIMARY KEY,
            sha256 TEXT,
            status TEXT,
            catalog_category TEXT,
            proposed_category TEXT,
            scanned_at TEXT
        )
        """
    )
    con.execute("INSERT INTO inventory_files VALUES (1, 'hash_bank_1', 'filed', 'Banking', 'Banking', '2026-11-14T00:00:00Z')")
    con.commit()
    con.close()

    return {
        "app_db": app_db,
        "ocr_db": ocr_db,
        "media_db": media_db,
        "inv_db": inv_db,
        "tok_todos": tok_todos,
        "tok_docs": tok_docs,
        "tok_revoked": tok_revoked,
    }


def test_session_auth_validation(isolated_env: dict[str, Path]) -> None:
    auth = resolve_auth_from_session("sess_active_user1", app_db=isolated_env["app_db"])
    assert auth.owner == "alice"
    assert "docs:read" in auth.scopes

    with pytest.raises(PermissionError, match="expired"):
        resolve_auth_from_session("sess_expired_user1", app_db=isolated_env["app_db"])

    with pytest.raises(PermissionError, match="archived"):
        resolve_auth_from_session("sess_archived_user1", app_db=isolated_env["app_db"])

    with pytest.raises(PermissionError, match="Invalid"):
        resolve_auth_from_session("nonexistent_session", app_db=isolated_env["app_db"])


def test_api_token_scopes_and_revocation(isolated_env: dict[str, Path]) -> None:
    # Existing todos:read token must be rejected for docs and media
    todos_auth = resolve_auth_from_token(isolated_env["tok_todos"], app_db=isolated_env["app_db"])
    assert todos_auth.scopes == frozenset({"todos:read"})
    with pytest.raises(PermissionError, match="docs:read"):
        search_local_intelligence(
            "podcast",
            auth=todos_auth,
            domains=("docs",),
            ocr_db=isolated_env["ocr_db"],
            media_db=isolated_env["media_db"],
            inventory_db=isolated_env["inv_db"],
        )

    # Revoked token fails closed
    with pytest.raises(PermissionError, match="revoked"):
        resolve_auth_from_token(isolated_env["tok_revoked"], app_db=isolated_env["app_db"])


def test_unauthorized_workspace_and_tier_enforcement(isolated_env: dict[str, Path]) -> None:
    # Token with max tier restricted-financial must NOT retrieve restricted-pii docs
    reader_auth = resolve_auth_from_token(isolated_env["tok_docs"], app_db=isolated_env["app_db"])
    res = search_local_intelligence(
        "podcast",
        auth=reader_auth,
        domains=("docs",),
        ocr_db=isolated_env["ocr_db"],
        media_db=isolated_env["media_db"],
        inventory_db=isolated_env["inv_db"],
    )
    assert "restricted-pii" not in res["allowed_sensitivity_tiers"]
    filenames = {r["filename"] for r in res["results"]}
    assert "storage_info.pdf" not in filenames
    assert "stmt_2026.pdf" in filenames

    # Mismatched owner fails closed (multi-user safety)
    with pytest.raises(PermissionError, match="does not match"):
        search_local_intelligence(
            "podcast",
            auth=reader_auth,
            expected_workstation_owner="bob",
            ocr_db=isolated_env["ocr_db"],
            media_db=isolated_env["media_db"],
            inventory_db=isolated_env["inv_db"],
        )


def test_cross_domain_rrf_ranking_and_contextual_redaction(isolated_env: dict[str, Path]) -> None:
    session_auth = resolve_auth_from_session("sess_active_user1", app_db=isolated_env["app_db"])
    res = search_local_intelligence(
        "podcast",
        auth=session_auth,
        domains=("docs", "media"),
        limit=10,
        ocr_db=isolated_env["ocr_db"],
        media_db=isolated_env["media_db"],
        inventory_db=isolated_env["inv_db"],
    )
    assert res["ranking"] == {"method": "rrf", "k": 60}
    domains_found = {r["domain"] for r in res["results"]}
    assert domains_found == {"docs", "media"}

    by_file = {r["filename"]: r for r in res["results"]}
    # Restricted-PII snippet suppressed by default
    assert "RESTRICTED-PII SNIPPET SUPPRESSED" in by_file["storage_info.pdf"]["snippet"]
    assert "451963" not in by_file["storage_info.pdf"]["snippet"]

    # Contextual redaction masks Account Number, but preserves legitimate dates and dollar amounts
    bank_snip = by_file["stmt_2026.pdf"]["snippet"]
    assert "9876543210" not in bank_snip
    assert "[REDACTED]" in bank_snip
    assert "06/15/2026" in bank_snip
    assert "$1,250.00" in bank_snip


def test_false_positive_redaction_guard() -> None:
    benign = "Invoice #2026-09-01 for $1,499.99 paid on 11/14/2026 reference ID 4090."
    cleaned, was_redacted = sanitize_snippet(benign, sensitivity_tier="business-rag")
    assert was_redacted is False
    assert cleaned == benign


def test_sqlite_readonly_wal_and_concurrent_readers(isolated_env: dict[str, Path]) -> None:
    ocr_db = isolated_env["ocr_db"]

    # Hold an active WAL connection with an uncommitted/uncheckpointed frame so -wal and -shm exist on disk
    wal_holder = sqlite3.connect(str(ocr_db))
    wal_holder.execute("PRAGMA wal_autocheckpoint = 0;")
    wal_holder.execute("UPDATE ocr_documents SET category = category WHERE 1 = 0;")
    try:
        # Direct write attempt on ro_connect must fail
        con = ro_connect(ocr_db)
        try:
            with pytest.raises(sqlite3.OperationalError):
                con.execute("DELETE FROM ocr_documents")
        finally:
            con.close()

        # Concurrent read queries across WAL-enabled databases must succeed cleanly
        session_auth = resolve_auth_from_session("sess_active_user1", app_db=isolated_env["app_db"])

        def worker() -> int:
            out = search_local_intelligence(
                "podcast",
                auth=session_auth,
                ocr_db=isolated_env["ocr_db"],
                media_db=isolated_env["media_db"],
                inventory_db=isolated_env["inv_db"],
            )
            return out["result_count"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            counts = list(pool.map(lambda _: worker(), range(12)))
        assert all(c == 4 for c in counts)
    finally:
        wal_holder.close()


def test_cli_exposes_no_secret_flags() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "local_intelligence_gateway.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    help_text = proc.stdout.lower()
    assert "--api-token" not in help_text
    assert "--session-id" not in help_text
    assert "--token" not in help_text


# === CP-3B.1 REGRESSION TESTS ===
@pytest.mark.parametrize(
    "raw_snippet,secret_token",
    [
        ("Acount# 123456789", "123456789"),
        ("Account# 123456789", "123456789"),
        ("Acc0unt# 123456789", "123456789"),
    ],
)
def test_cp3b1_ocr_account_typo_redaction(raw_snippet: str, secret_token: str) -> None:
    for tier in ("personal-private", "business-rag"):
        cleaned, was_redacted = sanitize_snippet(raw_snippet, sensitivity_tier=tier)
        assert was_redacted is True
        assert secret_token not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.parametrize(
    "raw_snippet,secret_token,facility_detail",
    [
        ("Public Storage Building B Unit 0551 ?999999# Second Floor", "999999", "Unit 0551"),
        ("Main Entrance Gate c0de: 123456 North Keypad", "123456", "North Keypad"),
        ("Warehouse Side Door Access code: 123456 after 6pm", "123456", "Side Door"),
        ("Storage Locker Passc0de: #884422* Floor 2", "884422", "Floor 2"),
    ],
)
def test_cp3b1_access_credential_full_snippet_suppression(
    raw_snippet: str, secret_token: str, facility_detail: str
) -> None:
    for tier in ("personal-private", "business-rag"):
        cleaned, was_redacted = sanitize_snippet(raw_snippet, sensitivity_tier=tier)
        assert was_redacted is True
        assert cleaned == "[CREDENTIAL-BEARING SNIPPET SUPPRESSED ? AUTHORIZED OWNER MUST INSPECT SOURCE FILE DIRECTLY]"
        assert secret_token not in cleaned
        assert facility_detail not in cleaned


@pytest.mark.parametrize(
    "benign_text",
    [
        "Invoice #2026-11-14 for $1,499.99 paid on 06/15/2026 ref 4090",
        "Wells Fargo Business Checking Statement Period 09/01/2026 to 09/30/2026 Ending Balance $14,280.55",
        "Client video editing deliverable approved on 2026-10-04 for $650.00",
    ],
)
def test_cp3b1_preserves_legitimate_financials_and_dates(benign_text: str) -> None:
    for tier in ("personal-private", "business-rag"):
        cleaned, was_redacted = sanitize_snippet(benign_text, sensitivity_tier=tier)
        assert was_redacted is False
        assert cleaned == benign_text


# === CP-4C.1 BOUNDARY REGRESSION TESTS ===
@pytest.mark.parametrize(
    "ordinary_text",
    [
        "We updated our password policy to require 16 characters.",
        "Review the company password rotation guidelines before Q4.",
        "Drop a pin at the studio location on the map for Thursday.",
        "Please bring your bank card and remember your PIN at the ATM.",
        "Security pin badge for the conference lanyard.",
    ],
)
def test_cp4c1_preserves_ordinary_password_and_pin_prose(ordinary_text: str) -> None:
    for tier in ("personal-private", "business-rag"):
        cleaned, was_redacted = sanitize_snippet(ordinary_text, sensitivity_tier=tier)
        assert was_redacted is False
        assert cleaned == ordinary_text


@pytest.mark.parametrize(
    "secret_text,secret_value",
    [
        ("Building entry PIN: 4829 after 7 PM.", "4829"),
        ("Building entry PIN 4829 after 7 PM.", "4829"),
        ("Wi-Fi Password = Summer2026! on the router sticker.", "Summer2026!"),
        ("Backup Passw0rd Summer2026 on file.", "Summer2026"),
        ("Main Gate c0de: 123456 North Keypad", "123456"),
        ("Unit 0551 ?999999# Second Floor", "999999"),
    ],
)
def test_cp4c1_suppresses_actual_pin_and_password_credentials(secret_text: str, secret_value: str) -> None:
    for tier in ("personal-private", "business-rag"):
        cleaned, was_redacted = sanitize_snippet(secret_text, sensitivity_tier=tier)
        assert was_redacted is True
        assert cleaned == "[CREDENTIAL-BEARING SNIPPET SUPPRESSED ? AUTHORIZED OWNER MUST INSPECT SOURCE FILE DIRECTLY]"
        assert secret_value not in cleaned
