"""Literal punctuation in library queries must not become SQL wildcards."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.database import Document
from routes.document import document_routes
from tests.helpers.database import disposable_database


CASES = [
    ("100%", "100% complete", "1000 complete"),
    ("a_b", "a_b", "axb"),
    ("%", "price 10%", "price 100"),
    ("_", "a_b", "axb"),
    (r"path\notes", r"path\notes", "pathnotes"),
    (r"path\_tag", r"path\_tag", r"path\Xtag"),
    ("50%_done", "50%_done", "500Xdone"),
    ("can't", "can't stop", "cannot stop"),
    ("mixed case", "Case ordered Mixed", "Mixed without"),
]


@pytest.mark.parametrize("column", ["title", "current_content"])
@pytest.mark.parametrize("query,literal,lookalike", CASES)
@pytest.mark.asyncio
async def test_library_search_matches_literal_tokens(tmp_path, monkeypatch, column, query, literal, lookalike):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(document_routes, "SessionLocal", factory)
        with factory() as db:
            for doc_id, text, owner, active, archived in [
                ("literal", literal, "alice", True, False),
                ("lookalike", lookalike, "alice", True, False),
                ("foreign", literal, "bob", True, False),
                ("inactive", literal, "alice", False, False),
                ("archived", literal, "alice", True, True),
            ]:
                fields = {"title": "Document", "current_content": "Body", column: text}
                db.add(Document(id=doc_id, owner=owner, is_active=active, archived=archived,
                                language="text", version_count=1, **fields))
            db.commit()
        router = document_routes.setup_document_routes(MagicMock())
        endpoint = next(r.endpoint for r in router.routes if r.path == "/api/documents/library")
        request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
        result = await endpoint(request, search=query, language=None, sort="recent", offset=0, limit=20, archived=False)
        assert result["total"] == 1
        assert [row["id"] for row in result["documents"]] == ["literal"]
        archived = await endpoint(request, search=query, language=None, sort="recent", offset=0, limit=20, archived=True)
        assert [row["id"] for row in archived["documents"]] == ["archived"]


@pytest.mark.asyncio
async def test_literal_search_keeps_cross_field_and_pagination_behavior(tmp_path, monkeypatch):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(document_routes, "SessionLocal", factory)
        with factory() as db:
            for doc_id, title, content in [
                ("first", "100% ready", "done"),
                ("second", "done", "100% ready"),
                ("lookalike", "1000 ready", "done"),
                ("missing-term", "100% ready", "waiting"),
            ]:
                db.add(Document(id=doc_id, owner="alice", title=title, current_content=content,
                                is_active=True, archived=False, language="text", version_count=1))
            db.commit()
        router = document_routes.setup_document_routes(MagicMock())
        endpoint = next(r.endpoint for r in router.routes if r.path == "/api/documents/library")
        request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
        results = [await endpoint(request, search="100% done", language="text", sort="alpha",
                                  offset=offset, limit=1, archived=False) for offset in [0, 1]]
        assert all(result["total"] == 2 for result in results)
        assert {row["id"] for result in results for row in result["documents"]} == {"first", "second"}
