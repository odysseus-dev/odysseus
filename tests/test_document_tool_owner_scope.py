import asyncio
import sys
import types

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.document_tools import (
    _owned_document_query,
    set_active_document,
)
from types import SimpleNamespace


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
        self.requested_limit = args[0]
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
    assert query.requested_limit == 50


def test_manage_documents_search_broadens_to_any_term_after_no_strict_match(monkeypatch):
    docs = [
        SimpleNamespace(
            id="tennis-doc", title="Tennis on Tuesday?", language="email",
            current_content="Let's play tennis.", updated_at=None, created_at=None,
        ),
        SimpleNamespace(
            id="dinner-doc", title="Dinner plans", language="text",
            current_content="Choose a dinner date.", updated_at=None, created_at=None,
        ),
        SimpleNamespace(
            id="other-doc", title="Quarterly budget", language="text",
            current_content="Finance review.", updated_at=None, created_at=None,
        ),
    ]
    query = _Query(docs=docs)
    _install_database_stub(monkeypatch, "core.database", query)

    result = asyncio.run(
        TOOL_HANDLERS["manage_documents"](
            '{"action":"list","search":"tennis dinner"}', {"owner": "alice"}
        )
    )

    ids = {item["id"] for item in result["documents"]}
    assert ids == {"tennis-doc", "dinner-doc"}


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

    assert result["exit_code"] == 1
    assert "Requested document not found" in result["error"]
    assert ("id", "eq", "doc-bob") in query.filters
    assert ("owner", "eq", "alice") in query.filters


def test_update_document_rejects_unchanged_content(monkeypatch):
    doc = SimpleNamespace(
        id="doc-alice", owner="alice", title="Draft", language="markdown",
        current_content="Exactly the same.", version_count=1,
    )
    _install_database_stub(monkeypatch, "src.database", _Query(first_doc=doc))
    set_active_document("doc-alice")
    try:
        result = asyncio.run(
            TOOL_HANDLERS["update_document"]("Exactly the same.", {"owner": "alice"})
        )
    finally:
        set_active_document(None)

    assert result["exit_code"] == 1
    assert "unchanged" in result["error"]
    assert doc.version_count == 1


def test_edit_document_rejects_noop_find_replace(monkeypatch):
    doc = SimpleNamespace(
        id="doc-alice", owner="alice", title="Draft", language="markdown",
        current_content="Exactly the same.", version_count=1,
    )
    _install_database_stub(monkeypatch, "src.database", _Query(first_doc=doc))
    set_active_document("doc-alice")
    try:
        result = asyncio.run(TOOL_HANDLERS["edit_document"](
            "<<<FIND>>>\nExactly the same.\n<<<REPLACE>>>\nExactly the same.\n<<<END>>>",
            {"owner": "alice"},
        ))
    finally:
        set_active_document(None)

    assert "No edits applied" in result["error"]
    assert "identical" in result["error"]
    assert "none of the FIND blocks matched" not in result["error"]
    assert doc.version_count == 1


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


def test_suggest_document_accepts_reason_that_says_clearer():
    from src.agent_tools.document_tools import parse_suggest_blocks

    parsed = parse_suggest_blocks(
        "<<<FIND>>>\nvery good\n<<<SUGGEST>>>\nclear and actionable\n"
        "<<<REASON>>>\nThe wording is more specific and clearer.\n<<<END>>>"
    )
    assert len(parsed) == 1
    assert parsed[0]["replace"] == "clear and actionable"


def test_suggestion_ids_are_stable_for_content_but_distinct_across_suggestions():
    from src.agent_tools.document_tools import _stable_suggestion_id

    first = {"find": "wordy", "replace": "brief", "reason": "clarity"}
    second = {"find": "slow", "replace": "quick", "reason": "pace"}
    assert _stable_suggestion_id("doc-1", first) == _stable_suggestion_id("doc-1", first)
    assert _stable_suggestion_id("doc-1", first) != _stable_suggestion_id("doc-1", second)
    assert _stable_suggestion_id("doc-1", first) != _stable_suggestion_id("doc-2", first)


def test_suggestion_matches_only_requested_email_sentence(monkeypatch):
    doc = types.SimpleNamespace(id='draft', current_content=(
        'To: test@example.com\nIn-Reply-To: <fixture@example.com>\n'
        'X-Source-UID: 123\n---\n8am works for me.\nPrevious message stays.'))
    _install_database_stub(monkeypatch, 'src.database', _Query(first_doc=doc))
    set_active_document('draft')
    try:
        result = asyncio.run(TOOL_HANDLERS['suggest_document'](
            '<<<FIND>>>\n8am works for me.\n<<<SUGGEST>>>\nFriday at 8am works.\n<<<REASON>>>\nAdd day\n<<<END>>>',
            {'owner': 'alice'}))
    finally:
        set_active_document(None)
    assert result['suggestions'][0]['find'] == '8am works for me.'


def test_document_tool_dispatch_forwards_owner():
    source = open("src/tool_execution.py", encoding="utf-8").read()

    assert "_document_tool_dispatch(tool, content, session_id, owner)" in source

    # Also verify TOOL_HANDLERS has the expected entries
    for key in ("create_document", "update_document", "edit_document",
                "suggest_document", "manage_documents"):
        assert key in TOOL_HANDLERS, f"TOOL_HANDLERS missing key: {key}"
        assert callable(TOOL_HANDLERS[key]), f"TOOL_HANDLERS[{key!r}] is not callable"
