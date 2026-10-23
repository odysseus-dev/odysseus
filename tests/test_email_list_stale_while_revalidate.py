"""Email list stale-while-revalidate (SWR) regression tests.

A plain list view must never block ~2-5 s on IMAP while we already hold a
recent snapshot or a durable index: it should return the snapshot immediately
(with ``sync.stale``) and refresh in the background. Blocking IMAP only remains
for a genuine cold start (no snapshot AND no indexed rows).
"""
import asyncio
import pathlib

from routes.email import email_routes as er

SRC = pathlib.Path(er.__file__).read_text(encoding="utf-8")


def _list_endpoint():
    router = er.setup_email_routes()
    for route in router.routes:
        if getattr(route, "name", "") == "list_emails":
            return route.endpoint
    raise AssertionError("list_emails route not found")


def _call(endpoint, **over):
    kwargs = dict(
        folder="INBOX", limit=50, offset=0, filter="all", from_addr=None,
        account_id=None, has_attachments=0, date_from=None, date_to=None,
        cached_only=0, cache_bust=None, refresh=0, owner="pewds",
    )
    kwargs.update(over)
    return asyncio.run(endpoint(**kwargs))


def test_list_returns_index_snapshot_with_a_stale_marker_without_blocking_on_imap(monkeypatch):
    endpoint = _list_endpoint()
    monkeypatch.setattr(
        er, "_email_index_list",
        lambda owner, account_id, folder, filter_, limit, offset, has_att: (
            [{"uid": "11", "subject": "Indexed", "is_read": False}], 1, "2026-10-11T00:00:00Z",
        ),
    )
    # The synchronous path must never open IMAP; if it did the request would
    # raise instead of returning the index snapshot immediately.
    monkeypatch.setattr(er, "_imap_connect", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("SWR must not open IMAP when an index snapshot exists")))

    result = _call(endpoint, cache_bust="123")
    assert result["total"] == 1
    assert [e["uid"] for e in result["emails"]] == ["11"]
    assert result["sync"]["stale"] is True
    assert result["sync"]["source"] in ("index", "cache_stale")


def test_cold_start_with_no_snapshot_and_no_index_is_not_marked_stale(monkeypatch):
    # With neither a snapshot nor an index, SWR must NOT fire: the request
    # falls through to the real (blocking) path, so the response is not a
    # stale-marked snapshot.
    endpoint = _list_endpoint()
    monkeypatch.setattr(er, "_email_index_list", lambda *a, **k: ([], 0, None))
    monkeypatch.setattr(er, "_imap_connect", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("offline test")))
    try:
        result = _call(endpoint)
    except Exception:
        return  # the cold path legitimately attempted real IMAP
    assert isinstance(result, dict)
    assert not (result.get("sync") or {}).get("stale")


def test_swr_contract_is_wired_into_the_list_path():
    assert "_LIST_STALE" in SRC
    assert "def _list_stale_put" in SRC
    assert "def _schedule_list_refresh" in SRC
    assert 'sync_meta["source"] = "cache_stale"' in SRC
    assert 'sync_meta["stale"] = True' in SRC
    # The manual-refresh contract the UI relies on must stay intact.
    assert "manual_refresh = bool(refresh)" in SRC
    assert "_invalidate_list_cache(account_id, folder)" in SRC
    assert "cached_rows = {} if refresh else _email_index_rows" in SRC
