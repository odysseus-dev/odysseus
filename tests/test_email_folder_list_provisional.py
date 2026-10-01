"""The folder picker must never present invented folder names as real.

`/api/email/folders` answers a cold cache with a hardcoded placeholder list
(`INBOX`, `Sent`, `Archive`) so the picker is not empty. Two of those three
names are wrong on Office 365 (`Sent Items`) and on Gmail
(`[Gmail]/Sent Mail`), and selecting one asks IMAP for a mailbox that does not
exist — which is what made sent mail look permanently missing there.

The contract is: any payload that is not a real IMAP LIST carries
`provisional: true`, and the client refetches instead of trusting it.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def routes_source():
    return (_REPO / "routes" / "email_routes.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def library_source():
    return (_REPO / "static" / "js" / "emailLibrary.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inbox_source():
    return (_REPO / "static" / "js" / "emailInbox.js").read_text(encoding="utf-8")


# ── Server ────────────────────────────────────────────────────────

def test_every_placeholder_folder_payload_is_marked_provisional(routes_source):
    """Each hardcoded list must be flagged, or the client cannot tell it apart
    from a real one."""
    block = routes_source.split("async def list_folders(")[1].split("@router.")[0]
    placeholders = block.count('"folders": ["INBOX", "Sent", "Archive"]')
    assert placeholders == 2, "expected the cached-only and timeout fallbacks"
    assert block.count('"provisional": True') == placeholders


def test_stale_cache_payloads_are_marked_provisional(routes_source):
    block = routes_source.split("async def list_folders(")[1].split("@router.")[0]
    stale_branches = block.count('sync_meta["source"] = "folder_cache_stale"')
    assert stale_branches == 2
    assert block.count('payload["provisional"] = True') == stale_branches


def test_a_real_imap_listing_is_not_marked_provisional(routes_source):
    """A live LIST is the authoritative answer and must not trigger a refetch."""
    block = routes_source.split("def _list_folders_sync():")[1].split("try:")[0]
    assert "provisional" not in block
    assert '"source": "imap"' in block


def test_a_fresh_cache_hit_is_not_marked_provisional(routes_source):
    block = routes_source.split("cached = _folder_cache_get(account_id, owner)")[1]
    block = block.split("if cached_only:")[0]
    assert "provisional" not in block


# ── Client ────────────────────────────────────────────────────────

def test_the_library_refetches_live_when_the_list_is_provisional(library_source):
    assert "if (data.provisional && !live) {" in library_source
    assert "_loadFolders({ resetMissing, live: true })" in library_source


def test_the_live_refetch_drops_the_cached_only_flag(library_source):
    """`live` is what turns cached_only off; without it the retry loops on the
    same placeholder."""
    assert "cached_only: live ? undefined : 1," in library_source


def test_the_library_refetch_cannot_recurse(library_source):
    """The live answer must not trigger another live fetch."""
    guard = re.search(r"if \(data\.provisional && !live\)", library_source)
    assert guard, "the refetch must be guarded on `!live`"


def test_the_inbox_retries_a_provisional_list_once(inbox_source):
    assert "if (data.provisional && !_folderRetryDone) {" in inbox_source
    assert "_folderRetryDone = true;" in inbox_source


def test_the_inbox_retry_is_bounded(inbox_source):
    """A mailbox that keeps timing out must not become a retry loop."""
    assert "let _folderRetryDone = false;" in inbox_source
