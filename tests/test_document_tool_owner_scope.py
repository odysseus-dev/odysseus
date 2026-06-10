import asyncio
import sys
import types

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.document_tools import (
    _owned_document_query,
    set_active_document,
)


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return (self.name, "eq", value)

    def desc(self):
        return (self.name, "desc")

    def ilike(self, value):
        return (self.name, "ilike", value)


class _Document:
    id = _Column("id")
    owner = _Column("owner")
    is_active = _Column("is_active")
    title = _Column("title")
    language = _Column("language")
    updated_at = _Column("updated_at")


class _Query:
    def __init__(self, docs=None, first_doc=None):
        self.filters = []
        self.docs = docs or []
        self.first_doc = first_doc

    def filter(self, *clauses):
        self.filters.extend(clauses)
        return self

    def order_by(self, *args):
        return self

    def limit(self, *args):
        return self

    def all(self):
        return self.docs

    def first(self):
        return self.first_doc


class _Db:
    def __init__(self, query):
        self.query_obj = query

    def query(self, *args):
        return self.query_obj

    def close(self):
        pass


def _install_database_stub(monkeypatch, module_name, query):
    db = _Db(query)
    db_mod = types.ModuleType(module_name)
    db_mod.SessionLocal = lambda: db
    db_mod.Document = _Document
    db_mod.DocumentVersion = object
    db_mod.Session = object
    monkeypatch.setitem(sys.modules, module_name, db_mod)
    return db


def test_owned_document_query_rejects_missing_owner():
    query = _Query()

    assert _owned_document_query(query, _Document, None) is query
    assert False in query.filters


def test_owned_document_query_filters_to_owner():
    query = _Query()

    assert _owned_document_query(query, _Document, "alice") is query
    assert ("owner", "eq", "alice") in query.filters


def test_manage_documents_list_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "core.database", query)

    result = asyncio.run(
        TOOL_HANDLERS["manage_documents"]('{"action":"list"}', {"owner": "alice"})
    )

    assert result["documents"] == []
    assert ("owner", "eq", "alice") in query.filters


def test_manage_documents_read_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "core.database", query)

    result = asyncio.run(
        TOOL_HANDLERS["manage_documents"](
            '{"action":"read","document_id":"doc-bob"}', {"owner": "alice"}
        )
    )

    assert result["exit_code"] == 1
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_update_document_active_id_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "src.database", query)
    set_active_document("doc-bob")
    try:
        result = asyncio.run(
            TOOL_HANDLERS["update_document"]("new content", {"owner": "alice"})
        )
    finally:
        set_active_document(None)

    assert result["error"] == "No documents exist to update"
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_suggest_document_active_id_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "src.database", query)
    set_active_document("doc-bob")
    try:
        result = asyncio.run(
            TOOL_HANDLERS["suggest_document"](
                "<<<FIND>>>\nold\n<<<SUGGEST>>>\nnew\n<<<REASON>>>\nbetter\n<<<END>>>",
                {"owner": "alice"},
            )
        )
    finally:
        set_active_document(None)

    assert result["error"] == "Document doc-bob not found"
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_document_tool_dispatch_forwards_owner():
    source = open("src/tool_execution.py", encoding="utf-8").read()

    assert "_document_tool_dispatch(tool, content, session_id, owner)" in source

    # Also verify TOOL_HANDLERS has the expected entries
    for key in ("create_document", "update_document", "edit_document",
                "suggest_document", "manage_documents"):
        assert key in TOOL_HANDLERS, f"TOOL_HANDLERS missing key: {key}"
        assert callable(TOOL_HANDLERS[key]), f"TOOL_HANDLERS[{key!r}] is not callable"
