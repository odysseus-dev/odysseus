"""create_pdf attachment: tool result → stream event → saved assistant metadata."""

import json
from types import SimpleNamespace

from src.attachment_refs import attachment_refs_from_metadata

ATTACHMENT = {"id": "a" * 32 + ".pdf", "name": "Boot_fix.pdf", "mime": "application/pdf", "size": 3100}


def _event_payload(chunk):
    assert chunk.startswith("data: ") and chunk.endswith("\n\n")
    return json.loads(chunk[len("data: "):])


def test_successful_create_pdf_emits_an_attachments_event():
    from src.agent_loop import _pdf_attachment_event

    chunk = _pdf_attachment_event("create_pdf", {"output": "ok", "exit_code": 0, "attachment": ATTACHMENT})

    assert _event_payload(chunk) == {"type": "attachments", "data": [ATTACHMENT]}


def test_no_event_for_failures_other_tools_or_malformed_refs():
    from src.agent_loop import _pdf_attachment_event

    assert _pdf_attachment_event("create_pdf", {"error": "x", "exit_code": 1, "attachment": ATTACHMENT}) is None
    assert _pdf_attachment_event("web_search", {"output": "x", "exit_code": 0, "attachment": ATTACHMENT}) is None
    assert _pdf_attachment_event("create_pdf", {"output": "x", "exit_code": 0}) is None
    bad = dict(ATTACHMENT, id="../../etc/passwd")
    assert _pdf_attachment_event("create_pdf", {"output": "x", "exit_code": 0, "attachment": bad}) is None


def test_both_tool_execution_paths_emit_the_attachment_event():
    """Round-loop calls and approved calls (run before round 1) both attach the file."""
    import inspect
    from src.agent_loop import stream_agent_loop

    source = inspect.getsource(stream_agent_loop)
    assert "_pdf_attachment_event(block.tool_type, result)" in source
    assert "_pdf_attachment_event(approved.tool_name, approved_result)" in source


LONG_ANSWER = "## Fix Windows 0xc0000001\n\n" + "1. Boot from the USB stick and open Command Prompt.\n" * 12


def test_the_reply_is_the_pdf_document_with_its_heading_as_the_title():
    from src.agent_loop import _pdf_document_from_reply

    body = LONG_ANSWER.split("\n\n", 1)[1].strip()
    assert _pdf_document_from_reply(LONG_ANSWER) == ("Fix Windows 0xc0000001", body)
    thinking = "<think>plan the answer</think>\n" + LONG_ANSWER
    assert _pdf_document_from_reply(thinking) == ("Fix Windows 0xc0000001", body)
    assert _pdf_document_from_reply("A haiku.\n\nOld pond, a frog jumps.") == ("A haiku", "A haiku.\n\nOld pond, a frog jumps.")


def test_a_reply_that_only_asks_back_is_not_a_document():
    from src.agent_loop import _pdf_document_from_reply

    assert _pdf_document_from_reply("Sure — which text should go in the PDF?") is None
    assert _pdf_document_from_reply("<think>hm</think>") is None
    assert _pdf_document_from_reply("") is None


def test_writer_sees_only_its_prompt_and_the_plain_conversation():
    from src.agent_loop import _PDF_WRITER_PROMPT, _pdf_writer_messages
    from src.prompt_security import untrusted_context_message

    messages = [
        {"role": "system", "content": "agent prompt with tools"},
        untrusted_context_message("client runtime context", "## Current date and time\nToday is Thursday."),
        {"role": "user", "content": "how do I install a game?"},
        {"role": "assistant", "content": "1. Buy it.\n2. Install it."},
        {"role": "user", "content": "make that into a pdf"},
    ]

    assert _pdf_writer_messages(messages) == [
        {"role": "system", "content": _PDF_WRITER_PROMPT},
        {"role": "user", "content": "how do I install a game?"},
        {"role": "assistant", "content": "1. Buy it.\n2. Install it."},
        {"role": "user", "content": "make that into a pdf"},
    ]


def test_writer_handles_plain_pdf_requests_and_leaves_data_requests_to_the_agent():
    from src.agent_loop import _uses_pdf_writer

    def turn(*families):
        return SimpleNamespace(capabilities=frozenset(families))

    ok = dict(active_document=None, exact_approval=None, plan_mode=False, owner="o", session_id="s")
    assert _uses_pdf_writer(turn("pdf_export"), **ok)
    assert _uses_pdf_writer(turn("pdf_export", "shell_files"), **ok)
    # "minecraft server" adds cookbook_admin; it's a topic word, not a data source.
    assert _uses_pdf_writer(turn("pdf_export", "cookbook_admin"), **ok)
    # Personal families count only when the request points at the user's data.
    assert not _uses_pdf_writer(turn("pdf_export", "notes"), **ok, request_text="make a pdf of my notes")
    assert not _uses_pdf_writer(turn("pdf_export", "email"), **ok, request_text="put my last emails into a pdf")
    assert not _uses_pdf_writer(turn("pdf_export", "tasks"), **ok, request_text="mach eine pdf aus meinen offenen Aufgaben")
    assert _uses_pdf_writer(turn("pdf_export", "email"), **ok, request_text="write a polite email template as a pdf")
    assert _uses_pdf_writer(turn("pdf_export", "notes"), **ok, request_text="make a pdf with study notes on TCP")
    assert not _uses_pdf_writer(turn("pdf_export", "search_browser"), **ok)
    assert not _uses_pdf_writer(turn("notes"), **ok)
    assert not _uses_pdf_writer(turn("pdf_export"), **dict(ok, active_document={"id": "d"}))
    assert not _uses_pdf_writer(turn("pdf_export"), **dict(ok, owner=None))


def test_fallback_title_comes_from_the_first_heading_or_sentence():
    from src.agent_loop import _pdf_fallback_title

    assert _pdf_fallback_title(LONG_ANSWER) == "Fix Windows 0xc0000001"
    assert _pdf_fallback_title("Rats are clever. They learn mazes fast.") == "Rats are clever"
    assert _pdf_fallback_title("") == "Document"
    assert len(_pdf_fallback_title("word " * 60)) <= 80


def test_fallback_runs_only_for_pdf_turns_without_a_created_pdf():
    from src.agent_loop import _should_run_pdf_fallback
    from types import SimpleNamespace

    pdf_turn = SimpleNamespace(capabilities=frozenset({"pdf_export"}))
    other_turn = SimpleNamespace(capabilities=frozenset({"search_browser"}))
    ok = dict(pdf_attached=False, awaiting_user=False, owner="o", session_id="s")

    assert _should_run_pdf_fallback(pdf_turn, **ok)
    assert not _should_run_pdf_fallback(other_turn, **ok)
    assert not _should_run_pdf_fallback(None, **ok)
    assert not _should_run_pdf_fallback(pdf_turn, **dict(ok, pdf_attached=True))
    assert not _should_run_pdf_fallback(pdf_turn, **dict(ok, awaiting_user=True))
    assert not _should_run_pdf_fallback(pdf_turn, **dict(ok, session_id=None))


def test_real_pdf_requests_reach_the_writer():
    """Phrasings from the live app on 2026-10-29, including ones with "server"."""
    from src.agent_loop import _uses_pdf_writer
    from src.turn_contract import requested_capabilities

    ok = dict(active_document=None, exact_approval=None, plan_mode=False, owner="o", session_id="s")
    for message in (
        "i want a detailed explanation on how to set up a minecraft server completly in the command line on ubuntu as a pdf",
        "create a pdf explanation on how to set up a minecraft server in ubuntu make it very detailed",
        "make a list of bash commands and put them into a pdf",
        "kannst du mir eine Zusammenfassung über server machen. mach eine pdf daraus",
        "I need a summery of all the important network tasks a switch handles make that into a pdf",
    ):
        contract = SimpleNamespace(capabilities=requested_capabilities(message, []))
        assert _uses_pdf_writer(contract, **ok, request_text=message), (message, sorted(contract.capabilities))


def test_prose_reply_with_injected_memory_still_attaches_the_pdf(monkeypatch):
    """Memory and runtime context mark nearly every real turn untrusted; the fallback must still run."""
    import asyncio

    import src.agent_loop as agent_loop
    import src.agent_tools.pdf_tools as pdf_tools
    from src.prompt_security import untrusted_context_message
    from src.tool_policy import build_effective_tool_policy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import requested_capabilities, resolve_turn_contract

    message = "can you make that into a pdf"
    history = [
        {"role": "user", "content": "how do I fix boot error 0xc0000001?"},
        {"role": "assistant", "content": LONG_ANSWER},
    ]
    policy = build_effective_tool_policy(disabled_tools=set(), last_user_message=message)
    contract = resolve_turn_contract(
        capabilities=requested_capabilities(message, history), schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy, message=message, history=history,
    )

    seen_messages = []
    explanation = "I put the boot repair steps into the PDF."

    async def prose_only(_candidates, messages, **_kwargs):
        seen_messages.append(messages)
        writing = messages[0]["content"] == agent_loop._PDF_WRITER_PROMPT
        yield f'data: {json.dumps({"delta": LONG_ANSWER if writing else explanation})}\n\n'
        yield "data: [DONE]\n\n"

    created = []

    async def fake_create(self, content, ctx):
        created.append((json.loads(content), ctx))
        return {"output": "ok", "exit_code": 0, "attachment": ATTACHMENT}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", prose_only, raising=False)
    monkeypatch.setattr(pdf_tools.CreatePdfTool, "execute", fake_create)

    async def run():
        return [chunk async for chunk in agent_loop.stream_agent_loop(
            "http://localhost:11434/v1/chat/completions", "qwen2.5:latest",
            [untrusted_context_message("saved memory: pinned context", "Prefers short answers.")]
            + history + [{"role": "user", "content": message}],
            session_id="s1", owner="o", turn_contract=contract, tool_policy=policy,
        )]

    events = [_event_payload(c) for c in asyncio.run(run()) if c.startswith("data: {")]

    assert {"type": "attachments", "data": [ATTACHMENT]} in events
    assert created[0][0]["title"] == "Fix Windows 0xc0000001"
    assert created[0][0]["content_markdown"].startswith("1. Boot from the USB stick")
    assert created[0][1] == {"session_id": "s1", "owner": "o"}
    writer_messages = seen_messages[0]
    assert writer_messages[0] == {"role": "system", "content": agent_loop._PDF_WRITER_PROMPT}
    assert "Prefers short answers" not in json.dumps(writer_messages)
    assert writer_messages[-1] == {"role": "user", "content": message}

    # The document goes only into the PDF; the chat gets the model's explanation.
    chat_text = "".join(e["delta"] for e in events if "delta" in e and not e.get("thinking"))
    assert chat_text == explanation
    explain_messages = seen_messages[1]
    assert ATTACHMENT["name"] in explain_messages[0]["content"]
    assert "1. Boot from the USB stick" in explain_messages[0]["content"]
    assert explain_messages[-1] == {"role": "user", "content": message}
    assert [e["type"] for e in events if e.get("tool") == "create_pdf"] == ["tool_start", "tool_output"]
    metrics = next(e["data"] for e in events if e.get("type") == "metrics")
    assert metrics["tool_events"][0]["tool"] == "create_pdf"


def test_no_pdf_when_the_reply_only_asks_back(monkeypatch):
    import asyncio

    import src.agent_loop as agent_loop
    import src.agent_tools.pdf_tools as pdf_tools
    from src.tool_policy import build_effective_tool_policy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import requested_capabilities, resolve_turn_contract

    policy = build_effective_tool_policy(disabled_tools=set(), last_user_message="make a pdf")
    contract = resolve_turn_contract(
        capabilities=requested_capabilities("make a pdf", []), schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy, message="make a pdf", history=[],
    )

    async def question(_candidates, _messages, **_kwargs):
        yield f'data: {json.dumps({"delta": "Which text should go in the PDF?"})}\n\n'
        yield "data: [DONE]\n\n"

    created = []

    async def fake_create(self, content, ctx):
        created.append(content)
        return {"output": "ok", "exit_code": 0, "attachment": ATTACHMENT}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", question, raising=False)
    monkeypatch.setattr(pdf_tools.CreatePdfTool, "execute", fake_create)

    async def run():
        return [chunk async for chunk in agent_loop.stream_agent_loop(
            "http://localhost:11434/v1/chat/completions", "any-model",
            [{"role": "user", "content": "make a pdf"}],
            session_id="s1", owner="o", turn_contract=contract, tool_policy=policy,
        )]

    events = [_event_payload(c) for c in asyncio.run(run()) if c.startswith("data: {")]
    assert created == []
    assert "".join(e.get("delta", "") for e in events) == "Which text should go in the PDF?"


def test_stream_agent_loop_wires_the_fallback_at_completion():
    import inspect
    from src.agent_loop import stream_agent_loop

    source = inspect.getsource(stream_agent_loop)
    assert "_should_run_pdf_fallback(" in source
    assert "_pdf_attached = True" in source


def test_saved_assistant_message_carries_the_attachment(monkeypatch):
    import core.database
    import routes.chat_helpers as chat_helpers

    monkeypatch.setattr(core.database, "update_session_last_accessed", lambda _sid: None)
    monkeypatch.setattr(chat_helpers, "_append_sft_trace_record", lambda **_kw: None)
    sess = SimpleNamespace(model="qwen2.5", history=[], add_message=None)
    sess.add_message = sess.history.append
    session_manager = SimpleNamespace(save_sessions=lambda: None)

    chat_helpers.save_assistant_response(
        sess, session_manager, "s1", "Your PDF is ready.", {"model": "qwen2.5"},
        attachments=[ATTACHMENT],
    )

    metadata = sess.history[-1].metadata
    assert metadata["attachments"] == [ATTACHMENT]
    # The upload cleanup reads the same refs, so the PDF is kept while the message exists.
    assert [ref["attachment_id"] for ref in attachment_refs_from_metadata(metadata)] == [ATTACHMENT["id"]]


def test_messages_without_attachments_keep_metadata_unchanged(monkeypatch):
    import core.database
    import routes.chat_helpers as chat_helpers

    monkeypatch.setattr(core.database, "update_session_last_accessed", lambda _sid: None)
    monkeypatch.setattr(chat_helpers, "_append_sft_trace_record", lambda **_kw: None)
    sess = SimpleNamespace(model="qwen2.5", history=[])
    sess.add_message = sess.history.append

    chat_helpers.save_assistant_response(
        sess, SimpleNamespace(save_sessions=lambda: None), "s1", "hi", {"model": "qwen2.5"},
    )

    assert "attachments" not in sess.history[-1].metadata
