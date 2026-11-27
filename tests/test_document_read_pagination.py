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


def test_truncated_read_notice_and_schemas_explain_offset_pagination(documents):
    from src.agent_loop import TOOL_SECTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    body = "A" * (MAX_OUTPUT_CHARS + 4000)
    with database.SessionLocal() as db:
        db.query(database.Document).filter_by(id="owned-document").first().current_content = body
        db.commit()

    result = asyncio.run(TOOL_HANDLERS["manage_documents"](
        '{"action":"read","document_id":"owned-document"}',
        {"owner": "fixture-owner"},
    ))
    page = result["document"]
    rendered = format_tool_result("manage_documents: read", result)
    assert len(rendered) <= MAX_OUTPUT_CHARS
    assert page["truncated"] is True
    assert f"offset={page['next_offset']}" in rendered

    doc_schema = next(
        t["function"] for t in FUNCTION_TOOL_SCHEMAS if t.get("function", {}).get("name") == "manage_documents"
    )
    assert "next_offset" in doc_schema["description"]
    assert "offset" in TOOL_SECTIONS["manage_documents"]


def test_document_read_signature_collapses_clamped_limits_and_aliases():
    from src.agent_loop import (
        _blocked_document_read_result,
        _document_mutation_tool_block,
        _document_read_signature,
    )
    from src.tool_types import ToolBlock

    sig_default = _document_read_signature(
        "manage_documents",
        '{"action": "read", "document_id": "doc-42"}',
    )
    sig_alias_huge_limit = _document_read_signature(
        "manage_documents",
        '{"action": "view", "id": "doc-42", "offset": 0, "limit": 50000}',
    )
    sig_next_page = _document_read_signature(
        "manage_documents",
        '{"action": "read", "document_id": "doc-42", "offset": 9800}',
    )
    sig_small_limit = _document_read_signature(
        "manage_documents",
        '{"action": "read", "document_id": "doc-42", "offset": 0, "limit": 500}',
    )

    assert sig_default is not None
    assert sig_default == sig_alias_huge_limit
    assert sig_default != sig_next_page
    assert sig_default != sig_small_limit
    assert _document_read_signature("manage_documents", '{"action": "list"}') is None

    assert _document_mutation_tool_block(ToolBlock("edit_document", '{"document_id":"doc-42"}'))
    assert _document_mutation_tool_block(ToolBlock("manage_documents", '{"action":"delete","document_id":"doc-42"}'))
    assert not _document_mutation_tool_block(ToolBlock("manage_documents", '{"action":"read","document_id":"doc-42"}'))

    blocked_trunc = _blocked_document_read_result({
        "round": 1,
        "doc_id": "doc-42",
        "offset": 0,
        "truncated": True,
        "next_offset": 9840,
        "size": 25000,
    })
    assert blocked_trunc["blocked"] is True
    assert blocked_trunc["policy"] == "repeated_document_read"
    assert '"offset": 9840' in blocked_trunc["error"]

    blocked_full = _blocked_document_read_result({
        "round": 1,
        "doc_id": "doc-42",
        "offset": 0,
        "truncated": False,
        "next_offset": None,
        "size": 1200,
    })
    assert blocked_full["blocked"] is True
    assert "complete content" in blocked_full["error"]


def test_agent_loop_blocks_repeated_truncated_document_read_and_allows_next_offset(documents, monkeypatch):
    import src.agent_loop as agent_loop
    from src.tool_capabilities import ToolGateDecision
    from tests.runtime_evidence_helpers import authoritative_executor

    body = "".join(f"Entry {i:04d}: sample line content for duplicate scan\n" for i in range(400))
    assert len(body) > MAX_OUTPUT_CHARS * 2
    with database.SessionLocal() as db:
        db.query(database.Document).filter_by(id="owned-document").first().current_content = body
        db.commit()

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "tool_result_should_arm_gate", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        agent_loop.ToolRunSecurityContext,
        "decision_for",
        lambda self, *args, **kwargs: ToolGateDecision(True),
    )

    executed_offsets = []
    seen_next_offset = None

    async def execute(block, *args, **kwargs):
        nonlocal seen_next_offset
        if block.tool_type == "edit_document":
            return "edit_document", {"response": "Edited document", "exit_code": 0}
        res = await TOOL_HANDLERS[block.tool_type](block.content, {"owner": "fixture-owner"})
        doc_meta = res.get("document") or {}
        executed_offsets.append(doc_meta.get("offset"))
        if doc_meta.get("next_offset") is not None and seen_next_offset is None:
            seen_next_offset = doc_meta["next_offset"]
        return f"{block.tool_type}: {block.content[:80]}", res

    call_idx = 0

    async def stream(_candidates, messages, **kwargs):
        nonlocal call_idx
        step = call_idx
        call_idx += 1
        if step == 0:
            delta = '```manage_documents\n{"action": "read", "document_id": "owned-document"}\n```'
        elif step == 1:
            # Model tries to bypass truncation at offset=0 by passing a huge limit + prose.
            delta = (
                "Let me try reading with a higher limit.\n"
                '```manage_documents\n{"action": "view", "document_id": "owned-document", "limit": 50000}\n```'
            )
        elif step == 2:
            assert seen_next_offset is not None
            delta = (
                f'```manage_documents\n{{"action": "read", "document_id": "owned-document", "offset": {seen_next_offset}}}\n```'
            )
        elif step == 3:
            delta = '```edit_document\n<<<FIND>>>\nEntry 0000\n<<<REPLACE>>>\nEntry 0000 updated\n<<<END>>>\n```'
        elif step == 4:
            # Re-reading offset=0 after a document mutation must be allowed!
            delta = '```manage_documents\n{"action": "read", "document_id": "owned-document"}\n```'
        else:
            delta = "Checked pages, updated entry, and verified."
        yield f'data: {json.dumps({"delta": delta})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", stream)

    async def collect():
        return [
            chunk
            async for chunk in agent_loop.stream_agent_loop(
                "http://unused.test/v1",
                "test-model",
                [{"role": "user", "content": "Read document owned-document, update Entry 0000, and verify."}],
                max_rounds=7,
                relevant_tools={"manage_documents", "edit_document"},
                owner="fixture-owner",
            )
        ]

    chunks = asyncio.run(collect())
    events = [
        json.loads(c[6:])
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    ]
    blocked_events = [e for e in events if e.get("type") == "tool_retry_blocked"]
    assert len(blocked_events) == 1
    assert blocked_events[0]["reason"] == "repeated_document_read"
    assert executed_offsets == [0, seen_next_offset, 0]


