"""Large document pages survive the model-facing tool-output boundary."""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.constants import MAX_OUTPUT_CHARS
from src.tool_execution import format_tool_result
from core import database
from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.document_tools import get_active_document, set_active_document


@pytest.fixture
def documents(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'documents.db'}")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    saved_active = get_active_document()
    with factory() as db:
        db.add(database.Document(id="owned-document", owner="fixture-owner", title="Owned",
            language="text", current_content=""))
        db.commit()
    yield
    set_active_document(saved_active)
    engine.dispose()


@pytest.mark.parametrize("limit", [None, 37, 20_000])
def test_formatted_document_pages_reconstruct_the_entire_body(documents, limit):
    body = "".join(f"Row {i}: α, quote=\" and slash=\\\n" for i in range(1200))
    with database.SessionLocal() as db:
        db.query(database.Document).filter_by(id="owned-document").first().current_content = body
        db.commit()

    offset = 0
    chunks = []
    while True:
        args = {"action": "read", "document_id": "owned-document", "offset": offset}
        if limit is not None:
            args["limit"] = limit
        content = json.dumps(args)
        result = asyncio.run(TOOL_HANDLERS["manage_documents"](content, {"owner": "fixture-owner"}))
        page = result["document"]
        rendered = format_tool_result(f"manage_documents: {content[:80]}", result)
        end = page["next_offset"] if page["truncated"] else len(body)
        chunk = body[offset:end]
        assert chunk in rendered
        assert len(rendered) <= MAX_OUTPUT_CHARS
        assert rendered.endswith("\n```")
        if page["truncated"]:
            assert end > offset
            assert f"next_offset={end}" in rendered
        chunks.append(chunk)
        if not page["truncated"]:
            break
        offset = end
    assert "".join(chunks) == body


def test_document_result_cannot_bypass_the_output_cap():
    rendered = format_tool_result("manage_documents: read", {
        "response": "x" * (MAX_OUTPUT_CHARS * 2),
        "document": {"offset": 0, "next_offset": None},
        "exit_code": 0,
    })
    assert len(rendered) < MAX_OUTPUT_CHARS + 100


@pytest.mark.parametrize("body", ["", "short document"])
@pytest.mark.parametrize("offset", [0, 100])
def test_short_or_exhausted_read_has_no_continuation(documents, body, offset):
    with database.SessionLocal() as db:
        db.query(database.Document).filter_by(id="owned-document").first().current_content = body
        db.commit()
    result = asyncio.run(TOOL_HANDLERS["manage_documents"](json.dumps({
        "action": "read", "document_id": "owned-document", "offset": offset,
    }), {"owner": "fixture-owner"}))
    assert result["document"]["content"] == body[offset:]
    assert result["document"]["next_offset"] is None
    assert not result["document"]["truncated"]
    assert format_tool_result("manage_documents: read", result) == result["response"]


def test_oversized_header_fails_without_a_nonadvancing_page(documents):
    with database.SessionLocal() as db:
        db.query(database.Document).filter_by(id="owned-document").first().title = "x" * MAX_OUTPUT_CHARS
        db.commit()
    result = asyncio.run(TOOL_HANDLERS["manage_documents"](
        '{"action":"read","document_id":"owned-document"}', {"owner": "fixture-owner"}))
    assert result["exit_code"] == 1
    assert "too long" in result["error"]
