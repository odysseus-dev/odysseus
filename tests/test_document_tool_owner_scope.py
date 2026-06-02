import asyncio
import sys
import types

from src import tool_implementations as tools


def test_closing_doc_b_does_not_clear_active_doc_a():
    tools._closed_document_ids.clear()
    tools.set_active_document("doc-a")
    try:
        assert tools.clear_active_document("doc-b") is False
        assert tools.get_active_document() == "doc-a"
    finally:
        tools.set_active_document(None)
        tools._closed_document_ids.clear()


def test_setting_closed_doc_reopens_it_for_context():
    tools._closed_document_ids.clear()
    tools.set_active_document("doc-a")
    try:
        assert tools.clear_active_document("doc-a") is True
        assert tools.get_active_document() is None
        assert "doc-a" in tools.get_closed_documents()

        tools.set_active_document("doc-a")
        assert "doc-a" not in tools.get_closed_documents()
    finally:
        tools.set_active_document(None)
        tools._closed_document_ids.clear()


def test_closed_document_ids_are_bounded():
    tools._closed_document_ids.clear()
    try:
        for i in range(tools._CLOSED_DOCUMENT_IDS_LIMIT + 5):
            tools.clear_active_document(f"doc-{i}")

        closed = tools.get_closed_documents()
        assert len(closed) == tools._CLOSED_DOCUMENT_IDS_LIMIT
        assert "doc-0" not in closed
        assert f"doc-{tools._CLOSED_DOCUMENT_IDS_LIMIT + 4}" in closed
    finally:
        tools.set_active_document(None)
        tools._closed_document_ids.clear()


class _ChatExpr:
    def __init__(self, op, field=None, value=None, inner=None):
        self.op = op
        self.field = field
        self.value = value
        self.inner = inner

    def __invert__(self):
        return _ChatExpr("not", inner=self)


class _ChatColumn:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return _ChatExpr("eq", self.name, value)

    def in_(self, values):
        return _ChatExpr("in", self.name, set(values))

    def desc(self):
        return ("desc", self.name)


class _ChatDocumentModel:
    id = _ChatColumn("id")
    owner = _ChatColumn("owner")
    session_id = _ChatColumn("session_id")
    is_active = _ChatColumn("is_active")
    updated_at = _ChatColumn("updated_at")


class _ChatDoc:
    def __init__(self, doc_id, *, owner="alice", session_id="session-a", is_active=True, updated_at=0):
        self.id = doc_id
        self.owner = owner
        self.session_id = session_id
        self.is_active = is_active
        self.updated_at = updated_at


def _chat_matches(doc, expr):
    if expr.op == "eq":
        return getattr(doc, expr.field) == expr.value
    if expr.op == "in":
        return getattr(doc, expr.field) in expr.value
    if expr.op == "not":
        return not _chat_matches(doc, expr.inner)
    raise AssertionError(f"unknown op {expr.op}")


class _ChatQuery:
    def __init__(self, docs):
        self.docs = docs
        self.filters = []
        self.sort_desc = None

    def filter(self, *clauses):
        self.filters.extend(c for c in clauses if isinstance(c, _ChatExpr))
        return self

    def order_by(self, *args):
        for arg in args:
            if isinstance(arg, tuple) and arg[0] == "desc":
                self.sort_desc = arg[1]
        return self

    def first(self):
        docs = [d for d in self.docs if all(_chat_matches(d, f) for f in self.filters)]
        if self.sort_desc:
            docs.sort(key=lambda d: getattr(d, self.sort_desc), reverse=True)
        return docs[0] if docs else None


class _ChatDb:
    def __init__(self, docs):
        self.docs = docs

    def query(self, *args):
        return _ChatQuery(self.docs)


def _chat_owner_filter(query, owner):
    return query.filter(_ChatDocumentModel.owner == owner)


def _reset_active_doc_state():
    tools.set_active_document(None)
    tools._closed_document_ids.clear()


def test_closed_doc_is_not_injected_by_session_fallback():
    _reset_active_doc_state()
    try:
        doc_a = _ChatDoc("doc-a", updated_at=10)
        tools.set_active_document("doc-a")
        tools.clear_active_document("doc-a")

        resolved = tools.resolve_active_document_for_chat(
            _ChatDb([doc_a]),
            _ChatDocumentModel,
            _chat_owner_filter,
            session_id="session-a",
            owner="alice",
            active_doc_closed=False,
        )

        assert resolved is None
    finally:
        _reset_active_doc_state()


def test_explicit_active_doc_reopens_closed_doc():
    _reset_active_doc_state()
    try:
        doc_a = _ChatDoc("doc-a", updated_at=10)
        tools.clear_active_document("doc-a")
        assert "doc-a" in tools.get_closed_documents()

        resolved = tools.resolve_active_document_for_chat(
            _ChatDb([doc_a]),
            _ChatDocumentModel,
            _chat_owner_filter,
            session_id="session-a",
            owner="alice",
            active_doc_id="doc-a",
            active_doc_closed=False,
        )

        assert resolved is doc_a
        assert tools.get_active_document() == "doc-a"
        assert "doc-a" not in tools.get_closed_documents()
    finally:
        _reset_active_doc_state()


def test_normal_closed_turn_does_not_clear_other_active_doc():
    _reset_active_doc_state()
    try:
        doc_a = _ChatDoc("doc-a", owner="alice", session_id="session-a")
        tools.set_active_document("doc-a")

        resolved = tools.resolve_active_document_for_chat(
            _ChatDb([doc_a]),
            _ChatDocumentModel,
            _chat_owner_filter,
            session_id="session-b",
            owner="bob",
            active_doc_closed=True,
        )

        assert resolved is None
        assert tools.get_active_document() == "doc-a"
        assert not tools.get_closed_documents()
    finally:
        _reset_active_doc_state()


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
