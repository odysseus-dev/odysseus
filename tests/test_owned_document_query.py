"""Tests for _owned_document_query owner scoping (src/tool_implementations.py)."""
from src.tool_implementations import _owned_document_query


class _FakeQuery:
    def __init__(self):
        self.filter_args = []

    def filter(self, *args):
        self.filter_args.append(args)
        return self


class _Doc:
    owner = "owner-column-sentinel"


def test_owner_none_does_not_pass_python_false():
    q = _FakeQuery()
    _owned_document_query(q, _Doc, None)
    arg = q.filter_args[-1][0]
    # The old code passed the bare Python bool False, which SQLAlchemy 2.x
    # rejects; the fix passes a SQL false() literal instead.
    assert arg is not False
    assert arg is not None


def test_owner_set_filters_by_owner():
    q = _FakeQuery()
    _owned_document_query(q, _Doc, "alice")
    assert q.filter_args, "should apply an owner filter"
