import asyncio
import sys
import types

from src import tool_implementations as tools


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

    assert tools._owned_document_query(query, _Document, None) is query
    assert False in query.filters


def test_owned_document_query_filters_to_owner():
    query = _Query()

    assert tools._owned_document_query(query, _Document, "alice") is query
    assert ("owner", "eq", "alice") in query.filters


def test_manage_documents_list_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "core.database", query)

    result = asyncio.run(tools.do_manage_documents('{"action":"list"}', owner="alice"))

    assert result["documents"] == []
    assert ("owner", "eq", "alice") in query.filters


def test_manage_documents_read_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "core.database", query)

    result = asyncio.run(
        tools.do_manage_documents('{"action":"read","document_id":"doc-bob"}', owner="alice")
    )

    assert result["exit_code"] == 1
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_update_document_active_id_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "src.database", query)
    tools.set_active_document("doc-bob")
    try:
        result = asyncio.run(tools.do_update_document("new content", owner="alice"))
    finally:
        tools.set_active_document(None)

    assert result["error"] == "No documents exist to update"
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_suggest_document_active_id_filters_to_calling_owner(monkeypatch):
    query = _Query()
    _install_database_stub(monkeypatch, "src.database", query)
    tools.set_active_document("doc-bob")
    try:
        result = asyncio.run(tools.do_suggest_document(
            "<<<FIND>>>\nold\n<<<SUGGEST>>>\nnew\n<<<REASON>>>\nbetter\n<<<END>>>",
            owner="alice",
        ))
    finally:
        tools.set_active_document(None)

    assert result["error"] == "Document doc-bob not found"
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_document_tool_dispatch_forwards_owner():
    source = open("src/tool_execution.py", encoding="utf-8").read()

    assert "do_create_document(content, session_id=session_id, owner=owner)" in source
    assert "do_update_document(content, owner=owner)" in source
    assert "do_edit_document(content, owner=owner)" in source
    assert "do_suggest_document(content, owner=owner)" in source
