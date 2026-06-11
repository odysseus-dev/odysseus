"""FTS session search must fetch hit rows in one query, not one per hit.

_search_fts looked up each FTS hit's full row with its own
db.query(...).filter(id == message_id).first(), an N+1 query. The lookup is now
a single batched IN(...) query via _fetch_messages_by_id.
"""
from src.session_search import _fetch_messages_by_id


class _Msg:
    def __init__(self, mid):
        self.id = mid


class _Query:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def all(self):
        self._calls["all"] += 1
        return self._rows


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.calls = {"query": 0, "all": 0}

    def query(self, *a, **k):
        self.calls["query"] += 1
        return _Query(self._rows, self.calls)


def test_batches_into_single_query():
    rows = [(_Msg("m1"), "Session One"), (_Msg("m2"), "Session Two")]
    db = _DB(rows)
    out = _fetch_messages_by_id(db, ["m1", "m2"])
    # One query for all hits, not one per hit.
    assert db.calls["query"] == 1
    assert db.calls["all"] == 1
    assert out["m1"][1] == "Session One"
    assert out["m2"][0].id == "m2"


def test_empty_ids_does_no_query():
    db = _DB([])
    assert _fetch_messages_by_id(db, []) == {}
    assert db.calls["query"] == 0
