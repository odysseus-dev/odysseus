"""A sent message must be visible in Sent immediately, not after the TTL.

The list route caches folder listings for a few seconds. The send path used to
invalidate only the *source* folder — the inbox message being replied to — so
switching to Sent right after sending showed a listing that predated the
message.
"""

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source():
    return (_REPO / "routes" / "email_routes.py").read_text(encoding="utf-8")


def _deliver_block(source: str) -> str:
    return source.split("def _deliver():")[1].split("if req.wait_for_delivery:")[0]


def test_the_sent_folder_cache_is_dropped_after_a_send(source):
    assert "_invalidate_list_cache(_account_id, sent_folder)" in _deliver_block(source)


def test_the_source_folder_is_still_invalidated(source):
    """Marking the replied-to message answered must keep refreshing its folder."""
    assert "_invalidate_list_cache(_account_id, _source_folder)" in _deliver_block(source)


def test_the_invalidation_runs_where_the_sent_folder_is_known(source):
    """It has to sit after the append, or `sent_folder` is not resolved yet."""
    block = _deliver_block(source)
    append_at = block.index("_ensure_sent_copy(")
    invalidate_at = block.index("_invalidate_list_cache(_account_id, sent_folder)")
    assert append_at < invalidate_at
