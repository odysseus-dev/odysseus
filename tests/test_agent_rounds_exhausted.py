"""Regression: stream_agent_loop emits `rounds_exhausted` only when the round
cap is hit while still working, and NOT on a normal finish.

The decision is a `for/else` in the loop: the `else` runs only if no `break`
fired (break = done / budget / error). A refactor that adds a stray break or
return, or moves the done-break, could silently flip this. See PR #1999 / #1997.
"""

import asyncio
import json
from pathlib import Path
import pytest

import src.agent_loop as al
from src.tool_capabilities import ToolGateDecision
from src.tool_capabilities import capabilities_for_action
from src.tool_approvals import tool_approval_store
from tests.runtime_evidence_helpers import authoritative_executor


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _types(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch):
    # Skip RAG/tool-index, MCP, and settings lookups; keep the real loop body,
    # _resolve_tool_blocks, and parse_tool_blocks.
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    # The round providers are synthetic. Keep real compaction logic while
    # supplying its context window instead of probing the dummy endpoint.
    import src.context_compactor as context_compactor
    monkeypatch.setattr(context_compactor, "get_context_length", lambda *a, **k: 128_000)
    # These fixtures supply the round provider below. Any direct grace-synthesis
    # request has no configured response, rather than contacting the fake URL.
    async def _unconfigured_direct_provider(*args, **kwargs):
        raise RuntimeError("No direct-provider completion configured in this fixture")
        yield  # Keep the direct-provider async-generator interface.
    monkeypatch.setattr(al, "stream_llm", _unconfigured_direct_provider)
    # These tests exercise round convergence. Keep the prompt-integrity gate
    # out of the fixture so a synthetic tool result does not turn the next
    # round into an approval test instead.
    monkeypatch.setattr(al, "tool_result_should_arm_gate", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(
        al.ToolRunSecurityContext,
        "decision_for",
        lambda self, *a, **k: ToolGateDecision(True),
        raising=False,
    )

    async def _fake_exec(block, *a, **k):
        return (block.tool_type, {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_loop(monkeypatch, round_text, max_rounds=2):
    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": round_text})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "do a long multi-step task"}],
        max_rounds=max_rounds,
            relevant_tools={"bash"},
    )
    return _types(_collect(gen))


def test_emits_rounds_exhausted_when_cap_hit_mid_task(monkeypatch):
    _patch_common(monkeypatch)
    # Use a system-owned interaction result so this remains a loop-control test:
    # Bash output is workspace-derived and now correctly pauses for exact user
    # approval before a later Bash call.
    events = _run_loop(
        monkeypatch,
        '```update_plan\n{"plan":"- [ ] keep going"}\n```',
        max_rounds=2,
    )
    assert any(e.get("type") == "rounds_exhausted" for e in events), events


def test_no_rounds_exhausted_on_normal_finish(monkeypatch):
    _patch_common(monkeypatch)
    # A plain answer (no tool block) -> done-break on round 1 -> no event.
    events = _run_loop(monkeypatch, "All done, here is your answer.", max_rounds=2)
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events


def test_python_html_mutation_queues_native_browser_verification(monkeypatch):
    _patch_common(monkeypatch)
    executed = []
    streams = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal streams
        streams += 1
        if streams == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "python-write",
                    "name": "python",
                    "arguments": json.dumps({
                        "code": "open('/workspace/output.html', 'w').write('<h1>ok</h1>')",
                    }),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"Created and verified the page."}\n\n'
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block.tool_type)
        return block.tool_type, {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    _collect(al.stream_agent_loop(
        "http://x/v1",
        "m",
        [{"role": "user", "content": "Create /workspace/output.html."}],
        max_rounds=2,
        relevant_tools={"python", "private_browser"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
        },
    ))

    assert executed[:2] == ["python", "private_browser"]


def test_unattended_native_round_exhaustion_gets_one_tool_free_final(monkeypatch):
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    synthesis_calls = []

    async def _fake_stream(_candidates, messages, **kwargs):
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": "last-inspection",
                "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/source.mp4"}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_synthesis(*args, **kwargs):
        synthesis_calls.append(kwargs)
        return "The evidence supports the final answer."

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the video and answer."}],
        max_rounds=1,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert len(synthesis_calls) == 1
    assert synthesis_calls[0]["max_retries"] == 1
    assert synthesis_calls[0]["thinking_mode"] == "off"
    assert synthesis_calls[0]["max_tokens"] <= 2048
    final = next(
        event for event in events
        if event.get("type") == "final_response"
        and event.get("fallback") == "unattended_round_exhaustion"
    )
    assert final["content"] == "The evidence supports the final answer."


def test_unattended_native_tool_turn_recovers_reasoning_preamble(monkeypatch):
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    stream_calls = []
    synthesis_calls = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        stream_calls.append(messages)
        if len(stream_calls) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "inspection",
                    "name": "inspect_media",
                    "arguments": json.dumps({"path": "/workspace/source.mp4"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: ' + json.dumps({
                "delta": "The user wants me to inspect the video. I need to review the frames."
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_synthesis(*args, **kwargs):
        synthesis_calls.append(kwargs)
        return "One event was visible in the inspected evidence."

    async def _fake_visual_exec(block, *args, **kwargs):
        return (
            block.tool_type,
            {
                "output": "timestamped contact sheets",
                "exit_code": 0,
                "images": [{"mimeType": "image/jpeg", "data": "pixels"}],
            },
        )

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_visual_exec, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the video and answer."}],
        max_rounds=8,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert 2 <= len(stream_calls) < 8
    assert synthesis_calls
    assert any(
        "last response was internal analysis" in str(call["messages"][-1]["content"]).lower()
        for call in synthesis_calls
    )
    assert any(
        "latest bounded visual observation" in str(call["messages"][-1]["content"]).lower()
        for call in synthesis_calls
    )
    recovery_content = next(
        call["messages"][-1]["content"]
        for call in synthesis_calls
        if "latest bounded visual observation" in str(
            call["messages"][-1]["content"]
        ).lower()
    )
    assert isinstance(recovery_content, list)
    assert recovery_content[-1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,pixels"},
    }
    assert not any(event.get("type") == "rounds_exhausted" for event in events)
    final = next(
        event for event in events
        if event.get("type") == "final_response"
        and event.get("fallback") == "unattended_preamble_recovery"
    )
    assert final["content"] == "One event was visible in the inspected evidence."


def test_unattended_native_tool_turn_recovers_empty_final(monkeypatch):
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    stream_calls = []
    synthesis_calls = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    async def _fake_stream(_candidates, messages, **kwargs):
        stream_calls.append(messages)
        if len(stream_calls) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "inspection",
                    "name": "inspect_media",
                    "arguments": json.dumps({"path": "/workspace/source.mp4"}),
                }],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_synthesis(*args, **kwargs):
        synthesis_calls.append(kwargs)
        return "Recovered from the gathered evidence."

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the video and answer."}],
        max_rounds=5,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert synthesis_calls
    assert "last response was empty" in str(
        synthesis_calls[0]["messages"][-1]["content"]
    ).lower()
    final = next(
        event for event in events
        if event.get("type") == "final_response"
        and event.get("fallback") == "unattended_empty_recovery"
    )
    assert final["content"] == "Recovered from the gathered evidence."


def test_unattended_force_answer_does_not_rearm_missing_tool_nudge(monkeypatch):
    _patch_common(monkeypatch)
    stream_calls = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    async def _fake_stream(_candidates, messages, **kwargs):
        stream_calls.append({"messages": messages, "tools": kwargs.get("tools")})
        if len(stream_calls) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "inspection",
                    "name": "inspect_media",
                    "arguments": json.dumps({"path": "/workspace/source.mp4"}),
                }],
            }) + "\n\n"
        elif len(stream_calls) == 2:
            yield 'data: ' + json.dumps({
                "delta": "Would you like me to search the web for the repository?"
            }) + "\n\n"
        else:
            yield 'data: ' + json.dumps({
                "delta": (
                    "The inspected evidence identifies the method. Its repository "
                    "status remains uncertain because web_fetch is unavailable."
                )
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the video and report whether its method has a repository."}],
        max_rounds=8,
        relevant_tools={"inspect_media", "web_fetch"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert len(stream_calls) == 3
    assert stream_calls[-1]["tools"] is None
    assert not any(event.get("type") == "rounds_exhausted" for event in events)


def test_native_explicit_script_run_uses_backend_bash_after_write(monkeypatch):
    _patch_common(monkeypatch)
    stream_count = 0
    executed = []

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal stream_count
        stream_count += 1
        if stream_count == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "write-script",
                    "name": "write_file",
                    "arguments": json.dumps({
                        "path": "/workspace/calculate.py",
                        "content": "print('ok')",
                    }),
                }],
            }) + "\n\n"
        elif stream_count == 2:
            yield 'data: ' + json.dumps({"delta": "The script is ready."}) + "\n\n"
        else:
            yield 'data: ' + json.dumps({"delta": "Created and ran the script."}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _capture_exec(block, *args, **kwargs):
        executed.append(block)
        return block.tool_type, {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _capture_exec, raising=False)

    _collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Write /workspace/calculate.py containing the calculation. "
                "Then run the script."
            ),
        }],
        max_rounds=5,
        relevant_tools={"write_file", "bash"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "interaction_mode": "cook",
        },
    ))

    assert any(
        block.tool_type == "bash"
        and block.content == "python /workspace/calculate.py"
        for block in executed
    )


def test_adaptive_turn_can_finish_after_more_than_legacy_coding_cap(monkeypatch):
    """Progressing coding work must not be cut off at the old 48-round cap."""
    _patch_common(monkeypatch)
    rounds = []

    async def _no_compaction(*args, **kwargs):
        return args[3], 0, False

    monkeypatch.setattr(al, "maybe_compact", _no_compaction, raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        n = len(rounds)
        if n <= 49:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": f"progress-{n}",
                    "name": "web_fetch",
                    "arguments": json.dumps({"url": f"https://example.com/page-{n}"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: ' + json.dumps({"delta": "Finished after all checks."}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        n = len(rounds)
        return ("web_fetch", {"output": f"progress-{n}", "text": f"progress-{n}", "exit_code": 0})

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "repair the parser"}],
        max_rounds=None,
        relevant_tools={"web_fetch"},
        owner="admin",
    )))

    assert len(rounds) == 50
    assert not any(event.get("type") == "rounds_exhausted" for event in events)
    assert any(event.get("type") == "completion_decision" for event in events)


def test_calendar_list_stops_after_first_synthesis_round(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0

    async def _fake_exec(block, *a, **k):
        assert block.tool_type == "manage_calendar"
        return "manage_calendar", {
            "output": (
                "Found 3 event(s):\n"
                "- Valentine's Day — 2027-02-13 (all day) #personal (Creator Ops)\n"
                "- Setsubun — 2027-02-27 (all day) #personal (Creator Ops)\n"
                "- Mochi's Birthday — 2027-08-02 (all day) #personal (Creator Ops)\n"
            ),
            "response": "Found 3 event(s)",
            "events": [
                {"uid": "ev-valentine", "summary": "Valentine's Day"},
                {"uid": "ev-setsubun", "summary": "Setsubun"},
                {"uid": "ev-mochi", "summary": "Mochi's Birthday"},
            ],
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "cal-1",
                    "name": "manage_calendar",
                    "arguments": json.dumps({
                        "action": "list_events",
                        "start": "2027-01-01T00:00:00",
                        "end": "2028-01-01T00:00:00",
                    }),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"Here are your 2027 calendar events: [Valentine](#event-ev-valentine), [Setsubun](#event-ev-setsubun), and [Mochi birthday](#event-ev-mochi)."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "kimi-k3",
        [{"role": "user", "content": "whats my calendar next year"}],
        max_rounds=4,
        relevant_tools={"manage_calendar"},
    )))

    assert calls == 2
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events
    assert [e.get("round") for e in events if e.get("type") == "agent_step"] == [2]
    visible = "\n".join(
        str(e.get("delta") or e.get("content") or "")
        for e in events
        if e.get("type") in {None, "final_response"}
    )
    assert visible.count("Here are your 2027 calendar events") <= 2


def test_topic_bulk_email_preemptive_runs_for_non_qwen_models(monkeypatch):
    _patch_common(monkeypatch)
    calls = []
    stream_calls = 0

    async def _fake_exec(block, *a, **k):
        calls.append(block)
        if block.tool_type == "mcp__email__search_emails":
            return block.tool_type, {
                "output": (
                    "Found 2 email(s):\n\n"
                    "1. **Traffic monitor stale**\n"
                    "   From: Alert Bot (alerts@example.com)\n"
                    "   Date: 2026-08-31T08:00:00+00:00\n"
                    "   UID: 301\n"
                    "   Account: Primary Inbox <alex@example.com>\n\n"
                    "2. **Traffic monitor active**\n"
                    "   From: Alert Bot (alerts@example.com)\n"
                    "   Date: 2026-08-31T09:00:00+00:00\n"
                    "   UID: 302\n"
                    "   Account: Primary Inbox <alex@example.com>\n"
                ),
                "exit_code": 0,
            }
        if block.tool_type == "mcp__email__bulk_email":
            return block.tool_type, {"output": "Bulk delete done for 2 email(s).", "exit_code": 0}
        raise AssertionError(f"unexpected tool: {block.tool_type}")

    async def _fake_stream(*args, **kwargs):
        nonlocal stream_calls
        stream_calls += 1
        yield 'data: {"delta":"model should not be needed"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://openrouter.ai/api/v1/chat/completions",
        "moonshotai/kimi-k3",
        [{"role": "user", "content": "delete all my traffic monitor emails"}],
        max_rounds=4,
        relevant_tools={"mcp__email__search_emails", "mcp__email__bulk_email"},
        owner="pewds",
    )))

    assert stream_calls == 0
    assert [block.tool_type for block in calls] == [
        "mcp__email__search_emails",
        "mcp__email__bulk_email",
    ]
    assert json.loads(calls[1].content) == {
        "action": "delete",
        "uids": ["301", "302"],
        "folder": "INBOX",
        "account": "alex@example.com",
    }
    final = next((event for event in events if event.get("type") == "final_response"), None)
    assert final is not None, events
    assert "Bulk delete done for 2 email(s)." in final["content"]


def test_notes_definition_question_suppresses_bad_notes_tool_call(monkeypatch):
    _patch_common(monkeypatch)
    executed = []

    async def _fake_exec(block, *a, **k):
        executed.append(block)
        return block.tool_type, {"output": "should not run", "exit_code": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": "notes-bad",
                "name": "manage_notes",
                "arguments": json.dumps({"action": "search", "query": "musical note"}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-notes-iteration3-merged",
        [{"role": "user", "content": "What is a musical note?"}],
        max_rounds=3,
        relevant_tools={"manage_notes"},
    )))

    assert executed == []
    final = next((event for event in events if event.get("type") == "final_response"), None)
    assert final is not None, events
    assert "written or sounded pitch" in final["content"]


def test_emits_intent_nudge_exhausted_when_cap_is_exhausted(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(monkeypatch, "Let me check the logs", max_rounds=5)

    guard = next((e for e in events if e.get("type") == "intent_nudge_exhausted"), None)
    assert guard is not None, events
    assert guard["reason"] == "intent_without_action_nudge_cap"
    assert guard["nudges"] == 2


def test_try_different_search_gets_intent_followthrough_nudges(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(
        monkeypatch,
        "Let me try a different search approach to find the official sources.",
        max_rounds=5,
    )

    guard = next(
        (event for event in events if event.get("type") == "intent_nudge_exhausted"),
        None,
    )
    assert guard is not None, events


def test_need_to_scan_gets_intent_followthrough_nudges(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(
        monkeypatch,
        "I need to scan through the video more carefully.",
        max_rounds=5,
    )

    assert any(
        event.get("type") == "intent_nudge_exhausted" for event in events
    ), events


def test_unattended_intent_nudge_cap_forces_final_answer(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            yield "data: " + json.dumps({"type": "tool_calls", "calls": [{
                "id": "inspect-1", "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/video.mp4"}),
            }]}) + "\n\n"
        elif any(
            "No user is available" in str(message.get("content") or "")
            for message in messages
        ):
            yield 'data: {"delta":"Best-supported answer: seven points were visible."}\n\n'
        else:
            yield 'data: {"delta":"I will analyze the remaining points."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect /workspace/video.mp4 and count the points."}],
        max_rounds=7,
        relevant_tools={"inspect_media"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
        },
    )))

    assert not any(e.get("type") == "intent_nudge_exhausted" for e in events)
    assert any("seven points" in str(e.get("delta") or "") for e in events)


def test_unattended_force_answer_rejects_long_planning_tail(monkeypatch):
    _patch_common(monkeypatch)
    import src.llm_core as llm_core

    synthesis_calls = []

    stream_count = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal stream_count
        stream_count += 1
        if stream_count == 1:
            yield "data: " + json.dumps({"type": "tool_calls", "calls": [{
                "id": "inspect-1", "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/video.mp4"}),
            }]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if any(
            "No user is available" in str(message.get("content") or "")
            for message in messages
        ):
            text = (
                "The contact sheets establish the players and several score changes. "
                * 12
                + "Let me continue examining the video at different times."
            )
        else:
            text = "I will analyze the remaining points."
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    async def _fake_synthesis(*args, **kwargs):
        synthesis_calls.append(kwargs)
        return "Best-supported answer: two events were visible."

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect /workspace/video.mp4 and count."}],
        max_rounds=5,
        relevant_tools={"inspect_media"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
        },
    )))

    assert synthesis_calls
    assert any(
        "two events" in str(event.get("delta") or event.get("content") or "")
        for event in events
    )


def test_unattended_optioned_clarification_forces_best_supported_answer(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            yield "data: " + json.dumps({"type": "tool_calls", "calls": [{
                "id": "inspect-1", "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/video.mp4"}),
            }]}) + "\n\n"
        elif len(seen) == 2:
            yield "data: " + json.dumps({
                "delta": (
                    "The clip appears incomplete. Would you like me to:\n"
                    "1. Count one segment?\n2. Search for another recording?"
                ),
            }) + "\n\n"
        else:
            yield 'data: {"delta":"Best-supported answer: the first rally has 12 shots."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect /workspace/video.mp4 and count the first rally."}],
        max_rounds=4,
        relevant_tools={"inspect_media"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
        },
    )))

    assert len(seen) == 3
    assert any("12 shots" in str(e.get("delta") or "") for e in events)


def test_emits_loop_breaker_triggered_when_loop_breaker_trips(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(
        monkeypatch,
        '```update_plan\n{"plan":"- [ ] keep going"}\n```',
        max_rounds=6,
    )

    guard = next((e for e in events if e.get("type") == "loop_breaker_triggered"), None)
    assert guard is not None, events
    assert guard["reason"] in {
        "consecutive_tool_failures",
        "loop_breaker_stall",
        "unchanged_tool_results",
    }


def test_stall_loop_breaker_is_bounded_after_forced_answer(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(
        monkeypatch,
        '```update_plan\n{"plan":"- [ ] keep going"}\n```',
        max_rounds=8,
    )

    guards = [e for e in events if e.get("type") == "loop_breaker_triggered"]
    assert guards, events
    assert len(guards) <= 2, events


def test_status_only_shell_detector_is_narrow():
    assert al._status_only_shell_command("echo 'blocked'")
    assert al._status_only_shell_command("test -x /tmp/tool && echo present || echo missing")
    assert not al._status_only_shell_command("echo ready > /tmp/status.txt")
    assert not al._status_only_shell_command("cat config.txt")


def test_read_only_inspection_detector_rejects_mutation():
    assert al._read_only_shell_command("pwd && find . -maxdepth 2 -type f")
    assert al._read_only_shell_command("ip route show default | head")
    assert not al._read_only_shell_command("sed -i 's/old/new/' config.txt")
    assert not al._read_only_shell_command("cat config.txt > copy.txt")


def test_actionable_request_detector_rejects_clarification_loop():
    assert al._looks_like_actionable_user_request(
        "Inspect the active workspace and tell me which file is present."
    )
    assert not al._looks_like_actionable_user_request(
        "What would you like me to do with the project?"
    )
    assert not al._looks_like_actionable_user_request("test now")
    assert not al._CLARIFICATION_ONLY_RESPONSE_RE.search(
        "Working — I'm here and ready. What would you like me to test?"
    )


def test_floor_plan_is_not_clamped_to_task_plan_tools():
    assert not al._looks_like_explicit_plan_request(
        "Reconstruct the floor plan layout of the room as a top-down diagram."
    )
    assert al._looks_like_explicit_plan_request(
        "Create a step-by-step plan for the migration."
    )


def test_same_round_duplicate_tool_calls_are_deduplicated_without_merging_distinct_calls():
    text_blocks, used_native, converted = al._resolve_tool_blocks(
        "```bash\npwd\n```\n```bash\npwd\n```\n```bash\nwhoami\n```",
        [],
        round_num=1,
    )
    assert not used_native
    assert [block.content for block in text_blocks] == ["pwd", "whoami"]
    assert converted == []

    native_calls = [
        {"id": "a", "name": "bash", "arguments": json.dumps({"command": "pwd"})},
        {"id": "b", "name": "bash", "arguments": json.dumps({"command": "pwd"})},
        {"id": "c", "name": "bash", "arguments": json.dumps({"command": "whoami"})},
    ]
    native_blocks, used_native, converted = al._resolve_tool_blocks(
        "", native_calls, round_num=1,
    )
    assert used_native
    assert [block.content for block in native_blocks] == ["pwd", "whoami"]
    assert [call["id"] for call in converted] == ["a", "c"]


def test_actionable_request_gets_one_nudge_after_clarification_only_response(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            yield 'data: {"delta":"Sure — what would you like me to do?"}\n\n'
        elif len(seen) == 2:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "call-1",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": "pwd"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"The workspace is ready."}\n\n'
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        return (block.tool_type, {"output": "/home/tester/project", "exit_code": 0})

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect the active workspace and tell me which file is present."}],
        max_rounds=4,
        relevant_tools={"host_shell"},
        workspace="/workspace",
    )))

    assert len(seen) == 3
    assert any(e.get("delta") == "The workspace is ready." for e in events)


def test_empty_workspace_round_gets_one_bounded_action_nudge(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            yield "data: [DONE]\n\n"
        elif len(seen) == 2:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "call-1",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": "pwd"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"Workspace checked."}\n\n'
        yield "data: [DONE]\n\n"

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        return (block.tool_type, {"output": "/workspace", "exit_code": 0})

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect the active workspace and update fixture.py."}],
        max_rounds=4,
        relevant_tools={"host_shell"},
        workspace="/workspace",
    )))

    assert len(seen) == 3
    decision = next(e["data"] for e in events if e.get("type") == "completion_decision")
    assert not decision["can_complete"]
    assert "fixture.py" in decision["missing_artifacts"]
    assert any(
        e.get("type") == "final_response"
        and "Workspace checked." in e.get("content", "")
        and "incomplete" in e.get("content", "")
        for e in events
    )


def test_empty_workspace_nudge_names_only_tools_in_active_schema(monkeypatch):
    directive = al._empty_action_tool_hint({"python", "read_file", "write_file"})
    assert "python" in directive
    assert "read_file" in directive
    assert "write_file" in directive
    assert "host_shell" not in directive
    assert "edit_file" not in directive
    assert "apply_patch" not in directive


def test_detached_host_shell_forces_poll_instead_of_substitute_command(monkeypatch):
    _patch_common(monkeypatch)
    seen_tools = []
    stream_round = 0

    async def _fake_stream(_candidates, _messages, **kwargs):
        nonlocal stream_round
        stream_round += 1
        if stream_round == 1:
            call = {
                "id": "start-1",
                "name": "host_shell",
                "arguments": json.dumps({"command": "build --long", "detach": True}),
            }
        elif stream_round == 2:
            # The model ignores the poll instruction and tries a substitute.
            call = {
                "id": "wrong-1",
                "name": "host_shell",
                "arguments": json.dumps({"command": "echo status"}),
            }
        else:
            yield 'data: {"delta":"Build finished."}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield "data: " + json.dumps({"type": "tool_calls", "calls": [call]}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        seen_tools.append((block.tool_type, block.content))
        if len(seen_tools) == 1:
            return "host_shell", {
                "output": "started",
                "status": "running",
                "detached": True,
                "job_id": "job-1",
                "exit_code": 0,
            }
        return "host_shell", {
            "output": "finished",
            "status": "completed",
            "job_id": "job-1",
            "exit_code": 0,
        }

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    _collect(al.stream_agent_loop(
        "http://x/v1",
        "router",
        [{"role": "user", "content": "Run the long build in this workspace."}],
        max_rounds=4,
        relevant_tools={"host_shell"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/workspace",
            "host_shell_bridge": {
                "url": "http://127.0.0.1:17654/run",
                "token": "test-token",
            },
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
    ))

    assert [tool for tool, _content in seen_tools] == ["host_shell", "host_shell"]
    assert json.loads(seen_tools[1][1]) == {"job_id": "job-1"}


def test_read_only_inspection_tool_round_requires_only_safe_tools():
    class Block:
        def __init__(self, tool_type, content):
            self.tool_type = tool_type
            self.content = content

    assert al._read_only_inspection_tool_round(
        [Block("host_shell", "pwd && ip route")]
    )
    assert not al._read_only_inspection_tool_round(
        [Block("host_shell", "make test")]
    )
    assert not al._read_only_inspection_tool_round(
        [Block("apply_patch", "update app.py")]
    )


def test_blocked_status_round_requires_a_blocked_claim_and_status_commands():
    class Block:
        tool_type = "bash"
        content = "echo 'cannot proceed'"

    assert al._blocked_status_tool_round([Block()], "The task is blocked; no source is available.")
    assert not al._blocked_status_tool_round([Block()], "The task is not blocked; continue.")


def test_blocked_status_loop_breaker_forces_convergence(monkeypatch):
    _patch_common(monkeypatch)
    events = _run_loop(
        monkeypatch,
        "The task is blocked; I cannot proceed.\n```bash\necho 'blocked'\n```",
        max_rounds=5,
    )

    guard = next((e for e in events if e.get("type") == "loop_breaker_triggered"), None)
    assert guard is not None, events
    assert guard["detail"] == "repeating blocked-task status commands without new progress"


def test_eval_workspace_prompt_uses_host_tool_then_answers(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_exec(block, *args, **kwargs):
        assert block.tool_type == "host_shell"
        return ("host_shell", {"output": "/home/tester/project", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append((list(messages), kwargs.get("tools") or []))
        if len(seen) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "call-1",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": "pwd"}),
                }],
            }) + "\n\n"
        else:
            tool_messages = [item for item in messages if item.get("role") == "tool"]
            assert tool_messages
            assert any("/home/tester/project" in str(item) or "host_shell" in str(item) for item in tool_messages)
            yield 'data: {"delta":"The active workspace is /home/tester/project."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "_tool_result_signature", lambda records: repr(records))

    routed = al._route_tui_local_workspace_tools(
        {"bash", "web_search"},
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
            "local_capability_contract": {"routing": {"local_workspace_first": True}},
        },
        text="Inspect the active workspace.",
        workspace=None,
    )
    assert "host_shell" in routed
    assert "bash" not in routed
    assert "web_search" not in routed

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Inspect the active workspace."}],
        max_rounds=4,
        relevant_tools={"host_shell", "ask_user", "update_plan"},
        disabled_tools=set(),
        workspace=None,
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "backend_host_limited": True,
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
        },
    )))

    assert len(seen) == 2
    assert any("active workspace is /home/tester/project" in e.get("delta", "") for e in events)


@pytest.mark.parametrize('explicit_verifier', [False, True])
def test_tui_coding_turn_recovers_inspects_patches_and_verifies(monkeypatch, explicit_verifier):
    """A stale compact router must still complete a real coding workflow."""
    _patch_common(monkeypatch)
    calls = []
    executed = []

    runtime_context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/odysseus-tui",
        "backend_host_limited": True,
        "host_shell_bridge": {
            "url": "http://host.docker.internal:17654/run",
            "token": "test-token",
        },
        "runtime_execution_contract": {
            "local_workspace_tasks": "use_host_shell_bridge",
        },
        "local_capability_contract": {
            "routing": {"local_workspace_first": True},
        },
    }

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        if block.tool_type == "host_shell":
            command = json.loads(block.content).get("command", "")
            if "pytest" in command or "npm test" in command or "make test" in command:
                return "host_shell", {
                    "output": "2 passed",
                    "exit_code": 0,
                }
            return "host_shell", {
                "output": (
                    "workspace=/home/tester/odysseus-tui\n"
                    "git_root=/home/tester/odysseus-tui\n"
                    "top_level:\napp.py\ntests/"
                ),
                "exit_code": 0,
            }
        if block.tool_type == "apply_patch":
            return "apply_patch", {
                "output": "Applied patch",
                "exit_code": 0,
                "diff": "--- a/app.py\n+++ b/app.py",
            }
        raise AssertionError(f"unexpected executed tool: {block.tool_type}")

    async def _fake_stream(_candidates, messages, **kwargs):
        calls.append((list(messages), kwargs.get("tools") or []))
        turn = len(calls)
        if turn == 1:
            # Reproduce the failure from the pasted trace: these names are
            # backend/container tools and are not part of the TUI surface.
            payload = {
                "type": "tool_calls",
                "calls": [
                    {"id": "stale-1", "name": "get_workspace", "arguments": "{}"},
                    {"id": "stale-2", "name": "ls", "arguments": "{}"},
                ],
            }
            yield "data: " + json.dumps(payload) + "\n\n"
        elif turn == 2:
            payload = {
                "type": "tool_calls",
                "calls": [{
                    "id": "patch-1",
                    "name": "apply_patch",
                    "arguments": json.dumps({
                        "patch": "*** Begin Patch\n*** Update File: app.py\n@@\n-old\n+new\n*** End Patch",
                    }),
                }],
            }
            yield "data: " + json.dumps(payload) + "\n\n"
        elif turn == 3:
            payload = {
                "type": "tool_calls",
                "calls": [{
                    "id": "test-1",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": "python -m pytest -q"}),
                }],
            }
            yield "data: " + json.dumps(payload) + "\n\n"
        else:
            yield 'data: {"delta":"Fixed the parser and verified the tests."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "deepseek-v4-flash",
        [{
            "role": "user",
            "content": "Fix the parser in this local TUI project and run "
                       + ("python -m pytest -q." if explicit_verifier else "the tests."),
        }],
        max_rounds=6,
        relevant_tools={"get_workspace", "ls", "host_shell", "apply_patch", "edit_file", "todowrite"},
        workspace=None,
        client_runtime_context=runtime_context,
    )))

    assert [tool for tool, _content in executed] == [
        "host_shell",
        "apply_patch",
        "host_shell",
    ]
    assert not any(tool in {"get_workspace", "ls"} for tool, _ in executed)
    # Invalid backend/container tool names are replaced with the authoritative
    # host-shell recovery in the same round, so no extra clarification round
    # should be required before the patch and verification turns. An opaque
    # conditional fallback still needs synthesis and cannot attest tests.
    assert len(calls) == (3 if explicit_verifier else 4)
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is explicit_verifier
    assert (decision['status'] == 'verified') is explicit_verifier
    assert any(
        event.get("type") == "final_response"
        and "Verification:" in event.get("content", "")
        and "passed" in event.get("content", "")
        for event in events
    ) is explicit_verifier
    terminal = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert terminal['completion_gate']['additional_provider_calls'] == 0
    assert not any(event.get("type") in {"rounds_exhausted", "loop_breaker_triggered"} for event in events)


def test_qwen_tui_coding_summary_reports_verification_retry(monkeypatch):
    """A verified coding turn summarizes tool evidence, not degraded prose."""
    _patch_common(monkeypatch)
    executed = []
    test_attempts = 0
    runtime_context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
        "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
    }

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        nonlocal test_attempts
        executed.append(block.tool_type)
        if block.tool_type == "read_file":
            return "read_file", {"output": "VALUE = 1\n", "exit_code": 0}
        if block.tool_type == "edit_file":
            return "edit_file", {"output": "Edited parser.py", "exit_code": 0}
        if block.tool_type == "host_shell":
            test_attempts += 1
            return "host_shell", {
                "output": "1 failed" if test_attempts == 1 else "1 passed",
                "exit_code": 1 if test_attempts == 1 else 0,
            }
        raise AssertionError(block.tool_type)

    async def _fake_stream(_candidates, messages, **kwargs):
        turn = len(executed) + 1
        if turn == 1:
            name, arguments = "read_file", {"path": "parser.py"}
        elif turn == 2:
            name, arguments = "edit_file", {
                "path": "parser.py", "old_string": "VALUE = 1", "new_string": "VALUE = 2",
            }
        else:
            name, arguments = "host_shell", {"command": "pytest -q"}
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{"id": f"call-{turn}", "name": name, "arguments": json.dumps(arguments)}],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "qwen35-9b-tool-router-v4-firstaction-noschema-adapter",
        [{"role": "user", "content": "Edit parser.py and run pytest -q."}],
        max_rounds=6,
        relevant_tools={"read_file", "edit_file", "host_shell"},
        client_runtime_context=runtime_context,
    )))

    assert executed == ["read_file", "edit_file", "host_shell", "host_shell"]
    summaries = [e.get("content", "") for e in events if e.get("type") == "final_response"]
    summary = summaries[-1]
    assert "Changed:" in summary and "`parser.py`" in summary
    assert "`pytest -q` failed" in summary
    assert "`pytest -q` passed" in summary


@pytest.mark.parametrize('explicit_verifier', [False, True])
def test_tui_coding_turn_replaces_duplicate_edit_with_requested_tests(monkeypatch, explicit_verifier):
    """A successful edit followed by another edit must converge on real tests."""
    _patch_common(monkeypatch)
    executed = []
    stream_round = 0
    runtime_context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "host_shell_bridge": {
            "url": "http://host.docker.internal:17654/run",
            "token": "x",
        },
        "runtime_execution_contract": {
            "local_workspace_tasks": "use_host_shell_bridge",
        },
    }

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        if block.tool_type == "read_file":
            return "read_file", {"output": "VALUE = 1\n", "exit_code": 0}
        if block.tool_type == "edit_file":
            return "edit_file", {"output": "Edited parser.py", "exit_code": 0}
        if block.tool_type == "host_shell":
            return "host_shell", {"output": "1 passed", "exit_code": 0}
        raise AssertionError(block.tool_type)

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal stream_round
        stream_round += 1
        if stream_round == 1:
            name, arguments = "read_file", {"path": "parser.py"}
        else:
            # The second call is the requested mutation. Every later model
            # response repeats it, reproducing the live DeepSeek failure.
            name, arguments = "edit_file", {
                "path": "parser.py",
                "old_string": "VALUE = 1",
                "new_string": "VALUE = 2",
            }
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{stream_round}",
                "name": name,
                "arguments": json.dumps(arguments),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "deepseek-v4-pro",
        [{
            "role": "user",
            "content": (
                "A regression was introduced in nested backend error handling. "
                "Find the cause, fix it with a scoped change, and run "
                + ("pytest -q." if explicit_verifier else "the relevant tests.")
            ),
        }],
        max_rounds=6,
        relevant_tools={"read_file", "edit_file", "host_shell"},
        client_runtime_context=runtime_context,
    )))

    assert [tool for tool, _ in executed] == [
        "read_file",
        "edit_file",
        "host_shell",
    ]
    verification_command = json.loads(executed[-1][1])["command"]
    assert "pytest" in verification_command
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is explicit_verifier
    assert (decision['status'] == 'verified') is explicit_verifier
    assert any(
        "Verification:" in event.get("content", "")
        and "passed" in event.get("content", "")
        for event in events
        if event.get("type") == "final_response"
    ) is explicit_verifier


def test_failed_forced_verifier_allows_an_adapted_test_command(monkeypatch):
    """The deterministic verifier runs once; a failure returns control to the model."""
    _patch_common(monkeypatch)
    executed = []
    stream_round = 0
    host_attempts = 0
    runtime_context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project-worktree",
        "host_shell_bridge": {
            "url": "http://host.docker.internal:17654/run",
            "token": "x",
        },
        "runtime_execution_contract": {
            "local_workspace_tasks": "use_host_shell_bridge",
        },
    }

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        nonlocal host_attempts
        executed.append((block.tool_type, block.content))
        if block.tool_type == "read_file":
            return "read_file", {"output": "VALUE = 1\n", "exit_code": 0}
        if block.tool_type == "edit_file":
            return "edit_file", {"output": "Edited parser.py", "exit_code": 0}
        if block.tool_type == "host_shell":
            host_attempts += 1
            if host_attempts == 1:
                return "host_shell", {"output": "collection error", "exit_code": 2}
            return "host_shell", {"output": "1 passed", "exit_code": 0}
        raise AssertionError(block.tool_type)

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal stream_round
        stream_round += 1
        if stream_round == 1:
            name, arguments = "read_file", {"path": "parser.py"}
        elif stream_round == 2:
            name, arguments = "edit_file", {
                "path": "parser.py",
                "old_string": "VALUE = 1",
                "new_string": "VALUE = 2",
            }
        elif stream_round == 3:
            # This is replaced by the one deterministic fallback verifier.
            name, arguments = "edit_file", {
                "path": "parser.py",
                "old_string": "VALUE = 1",
                "new_string": "VALUE = 2",
            }
        else:
            name, arguments = "host_shell", {
                "command": "/shared/.venv/bin/python -m pytest -q tests/test_parser.py",
            }
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{stream_round}",
                "name": name,
                "arguments": json.dumps(arguments),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "deepseek-v4-pro",
        [{
            "role": "user",
            "content": "Fix the parser regression and run the relevant tests.",
        }],
        max_rounds=7,
        relevant_tools={"read_file", "edit_file", "host_shell"},
        client_runtime_context=runtime_context,
    )))

    assert [tool for tool, _ in executed] == [
        "read_file",
        "edit_file",
        "host_shell",
        "host_shell",
    ]
    first_verifier = json.loads(executed[2][1])["command"]
    second_verifier = json.loads(executed[3][1])["command"]
    assert "git-common-dir" in first_verifier
    assert second_verifier.endswith("tests/test_parser.py")
    assert any(
        "Verification:" in event.get("content", "")
        and "passed" in event.get("content", "")
        for event in events
        if event.get("type") == "final_response"
    )


def test_later_tool_round_receives_deferred_compaction(monkeypatch):
    """Large tool transcripts must be compacted before the next model call."""
    _patch_common(monkeypatch)
    requests = []
    compaction_calls = []
    compaction_inputs = []

    async def _fake_compact(
        _session,
        endpoint_url,
        model,
        messages,
        headers=None,
        owner=None,
        *,
        persist=True,
        compaction_state=None,
    ):
        compaction_calls.append((len(compaction_calls) + 1, persist))
        compaction_inputs.append(list(messages))
        compacted = list(messages) + [{
            "role": "system",
            "content": "[Conversation summary] workspace task is still active; continue editing and verify.",
        }]
        if compaction_state is not None:
            compaction_state.update({
                "split_point": 1,
                "summary": "workspace task is still active; continue editing and verify.",
                "system_msg_count": 1,
                "applied": False,
            })
        return compacted, 32768, True

    async def _fake_stream(_candidates, messages, **kwargs):
        request_factory = kwargs.get("candidate_request_factory")
        if request_factory is not None:
            candidate_url, candidate_model, candidate_headers = _candidates[0]
            request = await request_factory(
                0,
                candidate_url,
                candidate_model,
                candidate_headers,
            )
            requests.append(request["messages"])
        else:
            requests.append(messages)
        if len(requests) == 1:
            yield 'data: {"delta":"```bash\\necho coding\\n```"}\n\n'
        else:
            yield 'data: {"delta":"Finished after verifying the change."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "maybe_compact", _fake_compact, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "m",
        [{"role": "user", "content": "edit the local project and run its tests"}],
        max_rounds=3,
        relevant_tools={"bash"},
    )))

    assert len(requests) == 2
    assert any(
        "[Conversation summary]" in str(message.get("content", ""))
        for message in requests[1]
    )
    assert len(compaction_calls) >= 1
    assert all(persist is False for _number, persist in compaction_calls)
    assert any(
        "echo coding" in str(message.get("content", ""))
        for message in compaction_inputs[-1]
    )
    assert any("Finished after verifying" in event.get("delta", "") for event in events)


def test_read_only_tui_inspection_is_bounded_but_edit_requests_are_not(monkeypatch):
    assert al._tui_read_only_inspection_turn(
        "Identify one parsing bug, report file/line evidence, and do not edit."
    )
    assert not al._tui_read_only_inspection_turn(
        "Inspect the parser and fix the concrete bug."
    )
    assert not al._tui_read_only_inspection_turn(
        "Inspect the repository, do not change tests, and repair the source files."
    )
    source = Path(al.__file__).read_text(encoding="utf-8")
    assert "_tui_turn_text = str(_last_user or \"\").strip()" in source
    directive = al._tui_read_only_inspection_directive()
    assert "one focused host_shell call" in directive
    assert "Do not edit" in directive
    network_directive = al._tui_local_network_directive()
    assert "one host_shell call" in network_directive
    assert "do not repeat" in network_directive
    assert "do not ping" in network_directive
    workspace_directive = al._tui_local_workspace_directive()
    assert "session_cwd" in workspace_directive
    assert "local_workspace_projects" in workspace_directive
    assert "apply_patch" in workspace_directive
    assert "shell redirects" in workspace_directive
    assert "detach=true" in workspace_directive
    assert "status=completed" in workspace_directive


def test_tui_host_find_with_pwd_prefix_is_bounded():
    context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
        "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
    }
    assert al._tui_bounded_host_read_command(
        "pwd && find . -name config.txt -type f"
    ) == (
        "pwd && find . -maxdepth 2 -name config.txt -type f",
        "find limited to -maxdepth 2",
    )
    assert al._tui_broad_host_read_reason(
        "pwd && find . -name config.txt -type f",
        client_runtime_context=context,
        workspace=None,
    )


def test_tui_host_bridge_rejects_broad_source_dumps(monkeypatch):
    context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
        "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
    }
    assert al._tui_broad_host_read_reason(
        "cd /home/tester/project && cat src/parser.py",
        client_runtime_context=context,
        workspace=None,
    )
    assert al._tui_broad_host_read_reason(
        "rg -n 'parse|tool_call' src/parser.py && sed -n '120,180p' src/parser.py",
        client_runtime_context=context,
        workspace=None,
    ) is None
    assert al._tui_broad_host_read_reason(
        "cat package.json 2>/dev/null || cat pyproject.toml 2>/dev/null",
        client_runtime_context=context,
        workspace=None,
    ) is None
    assert al._tui_broad_host_read_reason(
        "find . -type f",
        client_runtime_context=context,
        workspace=None,
    )


def test_tui_broad_host_reads_have_narrow_safe_equivalents():
    bounded = al._tui_bounded_host_read_command("find . -type f")
    assert bounded == ("find . -maxdepth 2 -type f", "find limited to -maxdepth 2")

    bounded = al._tui_bounded_host_read_command("cd src && cat parser.py")
    assert bounded == (
        "cd src && sed -n '1,240p' -- parser.py",
        "file read limited to lines 1-240",
    )

    assert al._tui_bounded_host_read_command("cat a.py b.py") is None
    assert al._tui_bounded_host_read_command("find . -maxdepth 3 -type f") is None


def test_substantive_answer_stops_after_all_failed_trailing_tools():
    records = [{"result": {"error": "malformed tool arguments", "exit_code": 1}}]
    assert al._substantive_answer_after_failed_tools("answer " * 100, records)


def test_short_answer_or_successful_tool_still_allows_recovery_round():
    failed = [{"result": {"error": "temporary failure", "exit_code": 1}}]
    successful = [{"result": {"output": "evidence", "exit_code": 0}}]
    assert not al._substantive_answer_after_failed_tools("short", failed)
    assert not al._substantive_answer_after_failed_tools("answer " * 100, successful)


def test_tui_broad_host_read_executes_bounded_equivalent(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    async def _fake_exec(block, *args, **kwargs):
        calls.append(block.content)
        return ("host_shell", {"output": "src/parser.py", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        if not calls:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "broad-read-1",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": "find . -type f"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"Found the focused file listing."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "_tool_result_signature", lambda records: repr(records))

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Inspect the local workspace."}],
        max_rounds=4,
        relevant_tools={"host_shell"},
        workspace=None,
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
            "local_capability_contract": {"routing": {"local_workspace_first": True}},
        },
    )))

    assert calls == ["find . -maxdepth 2 -type f"]
    assert not any(e.get("type") == "loop_breaker_triggered" for e in events)


def test_tui_local_skill_request_reconciles_normal_tool_policy(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    async def _fake_exec(block, *args, **kwargs):
        calls.append(block.tool_type)
        return (block.tool_type, {"output": "tdd skill loaded", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        if not calls:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "skill-call-1",
                    "name": "manage_skills",
                    "arguments": json.dumps({"action": "search", "query": "tdd"}),
                }],
            }) + "\n\n"
        else:
            yield 'data: {"delta":"The TDD skill is loaded."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Find the TDD skill and load it."}],
        max_rounds=4,
        relevant_tools={"manage_skills"},
        tool_policy=al.ToolPolicy(disabled_tools=frozenset({"manage_skills"})),
        workspace=None,
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
        },
    )))

    # Caller hard denials are absolute and cannot be re-enabled by later TUI turn contracts.
    assert calls == []
    assert not any(e.get("type") == "loop_breaker_triggered" for e in events)


def test_eval_identical_tool_evidence_converges_before_round_cap(monkeypatch):
    _patch_common(monkeypatch)
    rounds = []

    async def _fake_exec(block, *args, **kwargs):
        return ("host_shell", {"output": "unchanged evidence", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        n = len(rounds)
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{n}",
                "name": "host_shell",
                "arguments": json.dumps({"command": f"probe {n}"}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Inspect the workspace."}],
        max_rounds=8,
        relevant_tools={"host_shell"},
        workspace="/home/tester/project",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
        },
    )))

    guard = next(e for e in events if e.get("type") == "loop_breaker_triggered")
    assert guard["reason"] == "unchanged_tool_results"
    assert len(rounds) < 8


def test_identical_inspections_redirect_to_missing_artifact_before_convergence(monkeypatch):
    _patch_common(monkeypatch)
    rounds = []
    writes = []

    async def _fake_exec(block, *args, **kwargs):
        if "/tmp_workspace/results/report.md" in block.content:
            writes.append(block.content)
            return ("host_shell", {"output": "wrote /tmp_workspace/results/report.md", "exit_code": 0})
        return ("host_shell", {"output": "unchanged evidence", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        redirected = any(
            message.get("role") == "system"
            and "Stop repeating inspections" in str(message.get("content") or "")
            for message in messages
        )
        if redirected and not writes:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "write-required-artifact",
                    "name": "host_shell",
                    "arguments": json.dumps({
                        "command": (
                            "mkdir -p /tmp_workspace/results && printf '%s\\n' "
                            "'verified result' > /tmp_workspace/results/report.md"
                        ),
                    }),
                }],
            }) + "\n\n"
        elif writes:
            yield 'data: {"delta":"Created and verified the requested report."}\n\n'
        else:
            n = len(rounds)
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": f"probe-{n}",
                    "name": "host_shell",
                    "arguments": json.dumps({"command": f"probe {n}"}),
                }],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{"role": "user", "content": "Inspect the workspace."}],
        max_rounds=8,
        relevant_tools={"host_shell"},
        forced_tools={"host_shell"},
        workspace="/tmp_workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-tui",
            "terminal_agent": True,
            "session_cwd": "/tmp_workspace",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
            "local_capability_contract": {"routing": {"local_workspace_first": True}},
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/results/report.md"],
            },
        },
    )))

    assert len(writes) == 1
    assert any(event.get("type") == "completion_blocked" for event in events)
    assert not any(event.get("type") == "loop_breaker_triggered" for event in events)


def test_terminal_artifact_run_reserves_last_round_for_mutation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block.tool_type)
        if block.tool_type == "write_file":
            return block.tool_type, {
                "output": "wrote /workspace/report.csv",
                "exit_code": 0,
            }
        return block.tool_type, {
            "output": f"source evidence from {block.content}",
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = len(requests) == 3
        if recovering:
            call = {
                "id": "write-final",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "/workspace/report.csv",
                    "content": "source,status\nA,verified\n",
                }),
            }
        else:
            call = {
                "id": f"search-{len(requests)}",
                "name": "pdf_extract",
                "arguments": json.dumps({
                    "url": f"https://example.com/source-{len(requests)}.pdf",
                    "query": "publication status",
                }),
            }
        yield "data: " + json.dumps({"type": "tool_calls", "calls": [call]}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "qwen35-9b-policy-test",
        [{"role": "user", "content": "Research the sources and create /workspace/report.csv."}],
        max_rounds=3,
        relevant_tools={"pdf_extract", "write_file"},
        forced_tools={"pdf_extract", "write_file"},
        workspace="/workspace",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/workspace/report.csv"],
            },
        },
    )))

    assert executed == ["pdf_extract", "pdf_extract", "write_file"]
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_round_budget_reserved"
        for event in events
    )


def test_native_mutation_recovery_does_not_inherit_stale_acquisition_surface():
    selected = al._artifact_mutation_route_surface(
        mutation_surface={"write_file", "python"},
        capability_floor=set(),
        available_surface={"web_search", "web_fetch", "pdf_extract"},
        disabled_tools=set(),
        hard_blocked_tools=set(),
        native_terminal_runtime=True,
    )
    assert selected == {"write_file", "python"}

    external_selected = al._artifact_mutation_route_surface(
        mutation_surface={"write_file", "python"},
        capability_floor=set(),
        available_surface={"web_search", "web_fetch", "pdf_extract"},
        disabled_tools=set(),
        hard_blocked_tools=set(),
        native_terminal_runtime=False,
    )
    assert external_selected == set()


def test_native_request_scope_accepts_currently_offered_builtin_tools():
    external = [{"type": "function", "function": {"name": "web_search"}}]
    offered = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    assert al._request_scoped_allowed_tool_names(
        external, offered, native_terminal_runtime=True
    ) == {"web_search", "write_file"}
    assert al._request_scoped_allowed_tool_names(
        external, offered, native_terminal_runtime=False
    ) == {"web_search"}


def test_identical_failed_inspections_do_not_trigger_unchanged_evidence_recovery(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_exec(block, *args, **kwargs):
        return (block.tool_type, {"output": "not found", "exit_code": 1})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        content = json.dumps({"command": f"probe-{len(requests)}"})
        yield "data: " + json.dumps({
            "delta": f"```host_shell\n{content}\n```",
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect inputs and create the report."}],
        max_rounds=4,
        relevant_tools={"host_shell", "write_file"},
        forced_tools={"host_shell", "write_file"},
        workspace="/tmp_workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "external-agent",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert len(requests) == 4
    assert not any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_mutation_required"
        for event in events
    )


def test_post_redirect_varied_inspection_is_suppressed_until_artifact_mutation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block.content)
        if "report.md" in block.content and ">" in block.content:
            return ("host_shell", {"output": "wrote report.md", "exit_code": 0})
        return ("host_shell", {"output": "unchanged evidence", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        first_redirect = any(
            "Stop repeating inspections" in str(message.get("content") or "")
            for message in messages
        )
        mutation_only_retry = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if mutation_only_retry:
            command = "printf '%s\\n' verified > /tmp_workspace/report.md"
        elif first_redirect:
            command = f"strings source-{len(requests)}.bin | head"
        else:
            command = f"pwd && ls probe-{len(requests)}"
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{len(requests)}",
                "name": "host_shell",
                "arguments": json.dumps({"command": command}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{"role": "user", "content": "Inspect the workspace."}],
        max_rounds=9,
        relevant_tools={"host_shell"},
        forced_tools={"host_shell"},
        workspace="/tmp_workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-tui",
            "terminal_agent": True,
            "session_cwd": "/tmp_workspace",
            "host_shell_bridge": {
                "url": "http://host.docker.internal:17654/run",
                "token": "x",
            },
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert not any("strings source-" in command for command in executed)
    assert any("report.md" in command and ">" in command for command in executed)
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_mutation_required"
        for event in events
    )


def test_media_artifact_recovery_allows_bounded_source_inspection_before_mutation(
    monkeypatch,
):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "python":
            return "python", {
                "output": "created /workspace/floorplan.png",
                "exit_code": 0,
            }
        return "inspect_media", {
            "output": "additional source layout evidence",
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        inspections = sum(
            block.tool_type == "inspect_media" for block in executed
        )
        if not executed:
            name = "inspect_media"
            arguments = {
                "path": "/workspace/fixtures/source.mp4",
                "timestamp": "00:00:02",
                "output_path": "/workspace/source-frame.png",
            }
        elif inspections < 2:
            name = "inspect_media"
            arguments = {
                "path": "/workspace/fixtures/source.mp4",
                "start": "00:00:05",
                "end": "00:00:10",
                "frames": 4,
            }
        elif not any(block.tool_type == "python" for block in executed):
            name = "python"
            arguments = {
                "code": (
                    "from pathlib import Path\n"
                    "Path('/workspace/floorplan.png').write_bytes(b'png')\n"
                ),
            }
        else:
            yield "data: " + json.dumps({
                "delta": "Created the requested image from the inspected source.",
            }) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{len(requests)}",
                "name": name,
                "arguments": json.dumps(arguments),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Inspect /workspace/fixtures/source.mp4 and create "
                "/workspace/floorplan.png."
            ),
        }],
        max_rounds=6,
        relevant_tools={"inspect_media", "python", "write_file"},
        forced_tools={"inspect_media", "python", "write_file"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "loaded_files": [{"path": "/workspace/fixtures/source.mp4"}],
            "completion_requirements": {
                "required_artifacts": ["/workspace/floorplan.png"],
            },
        },
    )))

    assert [block.tool_type for block in executed] == [
        "inspect_media", "inspect_media", "python",
    ]
    assert not any(event.get("type") == "rounds_exhausted" for event in events)


def test_terminal_action_promise_is_not_treated_as_completion(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda _owner: set())
    requests = []
    writes = []

    async def _fake_exec(block, *args, **kwargs):
        if block.tool_type == "write_file":
            writes.append(block.content)
            return ("write_file", {"output": "created report.md", "exit_code": 0})
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield 'data: {"delta":"I need to inspect the files first."}\n\n'
        elif not writes:
            yield 'data: {"delta":"```write_file\\n/tmp_workspace/report.md\\ncomplete report\\n```"}\n\n'
        else:
            yield 'data: {"delta":"Completed the requested report."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Create the requested report."}],
        max_rounds=5,
        relevant_tools={"write_file"},
        forced_tools={"write_file"},
        workspace="/tmp_workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "external-agent",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert writes == ["/tmp_workspace/report.md\ncomplete report"]
    assert any(event.get("type") == "agent_step" for event in events)
    assert not any(event.get("type") == "completion_blocked" for event in events)


def test_terminal_create_promise_gets_one_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield 'data: {"delta":"Let me create a Python script to do this."}\n\n'
        else:
            yield 'data: {"delta":"The calculation is complete: 42."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Use Python to calculate the requested value."}],
        max_rounds=3,
        relevant_tools={"python"},
        forced_tools={"python"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
        },
    ))

    assert len(requests) == 2
    assert any(
        "DO IT NOW" in str(message.get("content") or "")
        for message in requests[1]
    )


def test_long_terminal_action_promise_gets_one_media_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({
                "delta": (
                    "The opening frames establish the court, players, and score display. "
                    "I compared the visible positions across several moments and can "
                    "already rule out the first two candidate sequences. The middle "
                    "portion contains the decisive action, but the final contact is too "
                    "small in the overview to report confidently. A closer frame will "
                    "separate the remaining possibilities and avoid guessing from motion "
                    "blur. This explanation is deliberately longer than the historical "
                    "short-response guard because real multimodal analyses often contain "
                    "substantial useful evidence before they stall.\n"
                    "Let me inspect the final frame now."
                ),
            }) + "\n\n"
        else:
            yield 'data: {"delta":"The final frame confirms sequence B."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and identify the sequence."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert len(requests) == 2
    assert any(
        "No successful local-media observation exists yet" in str(message.get("content") or "")
        for message in requests[1]
    )
    assert not any(event.get("type") == "intent_nudge_exhausted" for event in events)


def test_unattended_clarification_prose_gets_final_answer_nudge(monkeypatch):
    """Cook/native runs must not stop on a prose substitute for ask_user."""
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        text = (
            "I reviewed the available evidence. Would you like me to continue?"
            if len(requests) == 1
            else "The requested result is four events."
        )
        yield "data: " + json.dumps({"delta": text}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Review the evidence and report the result."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "interaction_mode": "cook",
            "unattended_mode": True,
            "terminal_agent": True,
        },
    )))

    assert len(requests) == 2
    assert any(
        "No user is available to answer follow-up questions"
        in str(message.get("content") or "")
        for message in requests[1]
    )
    assert any(
        "requested result is four events"
        in str(event.get("content") or event.get("delta") or "")
        for event in events
    )


def test_reexamine_terminal_action_promise_gets_media_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        text = (
            "The broad visual evidence suggests one interpretation, but the "
            "identity is not yet certain. But let me re-examine the visual "
            "evidence to be sure about who is shown."
            if len(requests) == 1
            else "The focused evidence confirms the requested identity."
        )
        yield "data: " + json.dumps({"delta": text}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and identify the subject."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert len(requests) == 2
    assert any(
        "No successful local-media observation exists yet" in str(message.get("content") or "")
        for message in requests[1]
    )
    assert any(
        "confirms the requested identity"
        in str(event.get("content") or event.get("delta") or "")
        for event in events
    )


def test_ill_need_to_examine_terminal_promise_gets_media_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({"type": "tool_calls", "calls": [{
                "id": "inspect-1", "name": "inspect_media",
                "arguments": json.dumps({"path": "/workspace/source.mp4"}),
            }]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        text = (
            "The overview is not precise enough for the requested count. "
            "I'll need to methodically examine the recording vicinity."
            if len(requests) == 2
            else "The focused review supports a count of four."
        )
        yield "data: " + json.dumps({"delta": text}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and count the events."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    )))

    assert len(requests) == 3


def test_refine_search_terminal_promise_gets_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        text = (
            "The first results discuss an unrelated meaning of the term. "
            "Let me refine my search queries to find the relevant sources."
            if len(requests) == 1
            else "The relevant sources confirm the requested publication status."
        )
        yield "data: " + json.dumps({"delta": text}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Research the publication status."}],
        max_rounds=3,
        relevant_tools={"web_search"},
        forced_tools={"web_search"},
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
        },
    )))

    assert len(requests) == 2


def test_long_media_answer_without_tool_evidence_is_not_accepted(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        yield "data: " + json.dumps({
            "delta": (
                "Let me inspect the final frame before deciding. "
                "The inspection shows a player serving from the near court while the "
                "receiver remains behind the baseline. The score graphic and subsequent "
                "celebration independently confirm the same point outcome. I compared "
                "the relevant frames and found no contradictory event between them. "
                "The requested sequence is therefore B, followed by D, and the evidence "
                "is sufficient to answer without another tool call."
            ),
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and identify the sequence."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    ))

    assert len(requests) == 2
    assert any(
        "No successful local-media observation exists yet" in str(message.get("content") or "")
        for message in requests[1]
    )


def test_long_dangling_answer_colon_gets_one_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({
                "delta": (
                    "The sampled frames show four distinct exchanges and make the first "
                    "three actions unambiguous. The fourth exchange starts after the camera "
                    "cut and ends with the player nearest the net. I checked the ordering "
                    "against the score overlay and reconciled the apparent discontinuity. "
                    "That leaves one consistent ordering across the complete clip, which "
                    "can now be stated directly. The requested visual order is:"
                ),
            }) + "\n\n"
        else:
            yield 'data: {"delta":"The visual order is A, B, C, then D."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and list the visual order."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    ))

    assert len(requests) == 2


def test_long_announced_answer_without_answer_gets_one_continuation(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        return block.tool_type, {
            "output": "Focused source evidence was collected.",
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "inspect-answer-promise",
                    "name": "inspect_media",
                    "arguments": json.dumps({
                        "path": "/workspace/source.mp4",
                        "frames": 8,
                    }),
                }],
            }) + "\n\n"
        elif len(requests) == 2:
            yield "data: " + json.dumps({
                "delta": (
                    "The sampled frames show a single object near the center of the scene. "
                    "Its narrow wheels, triangular frame, handlebars, and saddle are visible "
                    "across several angles. One view is partly occluded, but another clearly "
                    "shows the red frame and both wheels. The available visual evidence is "
                    "consistent enough to identify the object without another inspection. "
                    "I will provide the answer based on the current evidence."
                ),
            }) + "\n\n"
        else:
            yield 'data: {"delta":"The best-supported identification is a red bicycle."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect /workspace/source.mp4 and identify the visible object."}],
        max_rounds=3,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "loaded_files": [{"path": "/workspace/source.mp4"}],
        },
    ))

    assert len(executed) == 1
    assert len(requests) == 3
    recovery_messages = [str(message.get("content") or "") for message in requests[2]]
    assert any(
        "Give the concise final answer now" in str(message.get("content") or "")
        for message in requests[2]
    ), "\n---\n".join(recovery_messages[-8:])


def test_repeated_artifact_redirect_uses_bounded_body_handoff(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda _owner: set())
    import src.llm_core as llm_core

    requests = []
    writes = []
    synth_calls = []

    async def _fake_exec(block, *args, **kwargs):
        if block.tool_type == "write_file":
            writes.append(block.content)
            return ("write_file", {"output": "created report.md", "exit_code": 0})
        return (block.tool_type, {"output": "unchanged evidence", "exit_code": 0})

    async def _fake_synthesis(*args, **kwargs):
        synth_calls.append(kwargs)
        return "# Report\n\nVerified result from retained evidence."

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if writes:
            yield 'data: {"delta":"Created the requested report."}\n\n'
        else:
            yield "data: " + json.dumps({
                "delta": f"```bash\npwd && ls probe-{len(requests)}\n```",
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_synthesis, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the inputs and create a report."}],
        max_rounds=12,
        relevant_tools={"host_shell", "write_file"},
        forced_tools={"host_shell", "write_file"},
        workspace="/tmp_workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "external-agent",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "session_cwd": "/tmp_workspace",
            "host_shell_bridge": {
                "url": "http://host.docker.internal:17654/run",
                "token": "x",
            },
            "runtime_execution_contract": {
                "local_workspace_tasks": "direct_task_environment",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert len(synth_calls) == 1
    assert synth_calls[0]["max_retries"] == 1
    assert synth_calls[0]["thinking_mode"] == "off"
    assert synth_calls[0]["max_tokens"] <= 2048
    assert all(
        "Artifact recovery mode" not in str(message.get("content") or "")
        and "tool access" not in str(message.get("content") or "")
        for message in synth_calls[0]["messages"]
    )
    assert writes == [
        "/tmp_workspace/report.md\n# Report\n\nVerified result from retained evidence."
    ]
    assert any(event.get("type") == "artifact_body_handoff" for event in events)
    assert not any(event.get("type") == "rounds_exhausted" for event in events)


def test_artifact_body_synthesis_rejects_another_short_action_promise():
    assert al._artifact_body_from_synthesis("I need to inspect the files first.") == ""
    assert al._artifact_body_from_synthesis("```markdown\n# Complete\n\nResult\n```") == (
        "# Complete\n\nResult"
    )


def test_artifact_body_target_validation_rejects_long_prose_for_html():
    planning = (
        "Now I have the configuration. I will create a complete HTML file "
        "with CSS and JavaScript. " * 12
    )

    assert len(planning) >= 400
    assert al._artifact_body_from_synthesis(planning) == planning.strip()
    assert not al._artifact_body_matches_target(planning, "/workspace/output.html")
    assert al._artifact_body_matches_target(
        "<!doctype html><html><body>Done</body></html>",
        "/workspace/output.html",
    )


def test_artifact_body_target_validation_rejects_invalid_json():
    assert not al._artifact_body_matches_target("I will write the data next.", "result.json")
    assert al._artifact_body_matches_target('{"status": "done"}', "result.json")


def test_artifact_synthesis_messages_keep_user_evidence_but_drop_tool_systems():
    messages = al._artifact_synthesis_messages(
        [
            {"role": "system", "content": "You have tool access."},
            {"role": "user", "content": "Create the report from source A."},
            {"role": "assistant", "content": "I need to inspect it."},
            {"role": "user", "content": "Retained evidence: source A says 42."},
            {"role": "system", "content": "Artifact recovery mode is active."},
        ],
        "/tmp_workspace/report.md",
    )

    assert [message["role"] for message in messages] == [
        "system", "user", "user", "user"
    ]
    combined = "\n".join(str(message["content"]) for message in messages)
    assert "source A says 42" in combined
    assert "tool access" not in combined
    assert "Artifact recovery mode" not in combined


def test_terminal_adjacent_file_body_uses_workspace_write_not_personal_document(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda _owner: set())
    executed = []
    requests = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        return (block.tool_type, {"output": "wrote report.md", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if not executed:
            body = "# Report\n\n" + "\n".join(f"line {i}" for i in range(35))
            response = (
                "```bash\nwrite_file /tmp_workspace/report.md\n```\n\n"
                f"```markdown\n{body}\n```"
            )
            yield "data: " + json.dumps({"delta": response}) + "\n\n"
        else:
            yield 'data: {"delta":"Created and verified the requested report."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Create /tmp_workspace/report.md."}],
        max_rounds=3,
        relevant_tools={"bash", "write_file"},
        forced_tools={"bash", "write_file"},
        workspace="/tmp_workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "external-agent",
            "terminal_agent": True,
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert [block.tool_type for block in executed] == ["write_file"]
    assert executed[0].content.startswith("/tmp_workspace/report.md\n# Report")
    assert not any(event.get("tool") == "create_document" for event in events)
    assert not any(event.get("tool") == "manage_documents" for event in events)


def test_terminal_write_file_uses_exact_required_artifact_path(monkeypatch):
    _patch_common(monkeypatch)
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        return (block.tool_type, {"output": "wrote csv", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": "write-nested",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "/workspace/papers/meta_analysis.csv",
                    "content": "Model,RefCOCO-avg,ERQA\n",
                }),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Create /workspace/meta_analysis.csv."}],
        max_rounds=1,
        relevant_tools={"write_file"},
        forced_tools={"write_file"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "completion_requirements": {
                "required_artifacts": ["/workspace/meta_analysis.csv"],
            },
        },
    )))

    assert executed[0].tool_type == "write_file"
    assert executed[0].content.startswith("/workspace/meta_analysis.csv\n")


def test_terminal_stall_with_missing_artifact_enters_mutation_recovery(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block.content)
        return (
            "host_shell",
            {"output": f"evidence-{len(executed)}", "exit_code": 0},
        )

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        command = (
            "printf '%s\\n' complete > /tmp_workspace/report.md"
            if recovering
            else "python3 -c 'print(\"inspect\")'"
        )
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{len(requests)}",
                "name": "host_shell",
                "arguments": json.dumps({"command": command}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "_tool_result_signature", lambda records: repr(records))

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{"role": "user", "content": "Inspect the workspace."}],
        max_rounds=9,
        relevant_tools={"host_shell"},
        forced_tools={"host_shell"},
        workspace="/tmp_workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-tui",
            "terminal_agent": True,
            "session_cwd": "/tmp_workspace",
            "host_shell_bridge": {
                "url": "http://host.docker.internal:17654/run",
                "token": "x",
            },
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/report.md"],
            },
        },
    )))

    assert any("report.md" in command and ">" in command for command in executed)
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_mutation_required"
        for event in events
    )
    assert not any(event.get("type") == "loop_breaker_triggered" for event in events)


def test_blocked_ad_hoc_http_without_exit_code_enters_artifact_recovery(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "python":
            return (
                "python",
                {
                    "error": (
                        "python: ad-hoc HTTP access is disabled when native web tools "
                        "are available. Use pdf_extract for online PDFs, web_fetch for "
                        "a concrete page, or web_search for discovery."
                    )
                },
            )
        if block.tool_type == "pdf_extract":
            return (
                "pdf_extract",
                {
                    "output": (
                        "Resolved requested values by coordinate join: "
                        "Seed-1.5-VL-Thinking | RefCOCO-avg=91.3 | "
                        "requested metrics not found: ERQA"
                    ),
                    "exit_code": 0,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "pdf-1",
                    "name": "pdf_extract",
                    "arguments": json.dumps({
                        "url": "https://arxiv.org/pdf/2505.07062",
                        "query": "Seed-1.5-VL-Thinking RefCOCO-avg ERQA",
                    }),
                }],
            }) + "\n\n"
        elif len(requests) == 2:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "py-1",
                    "name": "python",
                    "arguments": json.dumps({
                        "command": "import requests; requests.get('https://arxiv.org/pdf/2505.07062')",
                    }),
                }],
            }) + "\n\n"
        else:
            assert any(
                "Artifact recovery mode is active" in str(message.get("content") or "")
                for message in messages
            )
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "write-1",
                    "name": "write_file",
                    "arguments": "/workspace/meta_analysis.csv\nModel,RefCOCO-avg,ERQA\nSeed-1.5-VL-Thinking,91.3,NaN\n",
                }],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Scan PDFs, create /workspace/meta_analysis.csv, and create "
                "/workspace/cross_benchmark_comparison.png."
            ),
        }],
        max_rounds=4,
        relevant_tools={"pdf_extract", "python", "write_file"},
        forced_tools={"pdf_extract", "python", "write_file"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": [
                    "/workspace/meta_analysis.csv",
                    "/workspace/cross_benchmark_comparison.png",
                ],
            },
        },
    )))

    assert [block.tool_type for block in executed][:2] == ["pdf_extract", "python"]
    assert len(requests) >= 3
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_wrong_tool_http"
        for event in events
    )


def test_artifact_recovery_suppresses_python_until_text_artifact_written(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "python":
            return (
                "python",
                {
                    "error": (
                        "python: ad-hoc HTTP access is disabled when native web tools "
                        "are available. Use pdf_extract for online PDFs."
                    ),
                    "exit_code": 1,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if len(requests) == 1:
            tool_name = "python"
            arguments = json.dumps({
                "command": "import requests; requests.get('https://arxiv.org/pdf/2505.07062')",
            })
        elif recovering and len(requests) == 2:
            # This mirrors the live r9 failure mode: the model ignores the
            # recovery instruction and tries Python again before writing CSV.
            tool_name = "python"
            arguments = json.dumps({
                "command": "import requests; requests.get('https://arxiv.org/pdf/2507.01006')",
            })
        else:
            tool_name = "write_file"
            arguments = json.dumps({
                "path": "/workspace/meta_analysis.csv",
                "content": (
                    "Model,RefCOCO-avg,ERQA\n"
                    "Qwen3-VL-A22B-Instruct,91.9,51.3\n"
                ),
            })
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"call-{len(requests)}",
                "name": tool_name,
                "arguments": arguments,
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Scan PDFs, create /workspace/meta_analysis.csv, and create "
                "/workspace/cross_benchmark_comparison.png."
            ),
        }],
        max_rounds=4,
        relevant_tools={"python", "write_file"},
        forced_tools={"python", "write_file"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": [
                    "/workspace/meta_analysis.csv",
                    "/workspace/cross_benchmark_comparison.png",
                ],
            },
        },
    )))

    assert [block.tool_type for block in executed].count("python") == 1
    assert any(block.tool_type == "write_file" for block in executed)
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_wrong_tool_http"
        for event in events
    )


def test_binary_artifact_recovery_keeps_generation_tools_for_png(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "write_file":
            return (
                "write_file",
                {
                    "output": "wrote /workspace/data.csv",
                    "exit_code": 0,
                },
            )
        if block.tool_type == "python":
            return (
                "python",
                {
                    "output": "created /workspace/comparison_radar.png",
                    "exit_code": 0,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if not recovering:
            tool_name = "write_file"
            arguments = json.dumps({
                "path": "/workspace/data.csv",
                "content": "metric,value\naccuracy,1\n",
            })
        else:
            tool_name = "python"
            arguments = json.dumps({
                "code": (
                    "from pathlib import Path\n"
                    "Path('/workspace/comparison_radar.png').write_bytes(b'png')\n"
                ),
            })
        yield (
            "data: "
            + json.dumps({"delta": f"```{tool_name}\n{arguments}\n```"})
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Use the local media/data inputs and create /workspace/data.csv "
                "and /workspace/comparison_radar.png."
            ),
        }],
        max_rounds=4,
        relevant_tools={"write_file", "python", "inspect_media"},
        forced_tools={"write_file", "python", "inspect_media"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "loaded_files": [{"path": "/workspace/input.png"}],
            "completion_requirements": {
                "required_artifacts": [
                    "/workspace/data.csv",
                    "/workspace/comparison_radar.png",
                ],
            },
        },
    )))

    assert [block.tool_type for block in executed] == ["write_file", "python"]
    assert any(
        "Artifact recovery mode is active" in str(message.get("content") or "")
        for messages in requests[1:]
        for message in messages
    )
    assert not any(
        event.get("type") == "tool_call_dropped"
        and event.get("tool") == "python"
        for event in events
    )


def test_final_prose_with_missing_artifact_enters_recovery(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "write_file":
            return (
                "write_file",
                {
                    "output": "wrote requested file",
                    "exit_code": 0,
                },
            )
        if block.tool_type == "python":
            return (
                "python",
                {
                    "output": "created /workspace/cross_benchmark_comparison.png",
                    "exit_code": 0,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if len(requests) <= 3:
            yield (
                "data: "
                + json.dumps({
                    "delta": (
                        "```write_file\n"
                        f"/workspace/partial_{len(requests)}.txt\n"
                        "Model,RefCOCO-avg,ERQA\n"
                        "Seed-1.5-VL-Thinking,91.3,N/A\n"
                        "```"
                    )
                })
                + "\n\n"
            )
        elif len(requests) == 4:
            yield (
                "data: "
                + json.dumps({
                    "delta": (
                        "Good, I wrote the CSV and chart-generation script. "
                        "Now I need to execute it to generate the PNG artifact."
                    )
                })
                + "\n\n"
            )
        elif recovering:
            yield (
                "data: "
                + json.dumps({
                    "delta": (
                        "```python\n"
                        "from pathlib import Path\n"
                        "Path('/workspace/cross_benchmark_comparison.png').write_bytes(b'png')\n"
                        "```"
                    )
                })
                + "\n\n"
            )
        else:
            yield "data: [DONE]\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Create /workspace/meta_analysis.csv and "
                "/workspace/cross_benchmark_comparison.png."
            ),
        }],
        max_rounds=6,
        relevant_tools={"write_file", "python"},
        forced_tools={"write_file", "python"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": [
                    "/workspace/meta_analysis.csv",
                    "/workspace/cross_benchmark_comparison.png",
                ],
            },
        },
    )))

    assert [block.tool_type for block in executed] == [
        "write_file",
        "write_file",
        "write_file",
        "python",
    ]
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") in {
            "final_response_missing_artifacts",
            "partial_artifact_mutation",
        }
        for event in events
    )
    assert not any(
        "Now I need to execute it" in str(event.get("content") or "")
        for event in events
        if event.get("type") == "final_response"
    )


def test_blocked_ad_hoc_http_prefers_native_acquisition_before_artifact_write(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "bash":
            return (
                "bash",
                {
                    "output": (
                        "bash: ad-hoc HTTP access is disabled when native web tools "
                        "are available. Use pdf_extract for online PDFs."
                    ),
                    "exit_code": 1,
                },
            )
        if block.tool_type == "pdf_extract":
            return (
                "pdf_extract",
                {
                    "output": (
                        'Resolved values JSON: {"model": "Seed-1.5-VL-Thinking", '
                        '"source": "pdf_extract_positioned_table", '
                        '"values": {"ERQA": null, "RefCOCO": "91.3"}}'
                    ),
                    "exit_code": 0,
                },
            )
        if block.tool_type == "write_file":
            return (
                "write_file",
                {
                    "output": "wrote /workspace/meta_analysis.csv",
                    "exit_code": 0,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            tool_name = "bash"
            arguments = "python - <<'PY'\nimport requests\nrequests.get('https://arxiv.org/pdf/2505.07062')\nPY"
        elif not any(block.tool_type == "pdf_extract" for block in executed):
            tool_name = "pdf_extract"
            arguments = json.dumps({
                "url": "https://arxiv.org/pdf/2505.07062",
                "query": "Seed-1.5-VL-Thinking RefCOCO ERQA",
            })
        else:
            tool_name = "write_file"
            arguments = json.dumps({
                "path": "/workspace/meta_analysis.csv",
                "content": (
                    "Model,RefCOCO-avg,ERQA\n"
                    "Seed-1.5-VL-Thinking,91.3,44.1\n"
                ),
            })
        yield (
            "data: "
            + json.dumps({"delta": f"```{tool_name}\n{arguments}\n```"})
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": (
                "Use https://arxiv.org/pdf/2505.07062 and create "
                "/workspace/meta_analysis.csv."
            ),
        }],
        max_rounds=4,
        relevant_tools={"bash", "pdf_extract", "web_fetch", "web_search", "write_file"},
        forced_tools={"bash", "pdf_extract", "web_fetch", "web_search", "write_file"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/workspace/meta_analysis.csv"],
            },
        },
    )))

    assert [block.tool_type for block in executed[:3]] == [
        "bash",
        "pdf_extract",
        "write_file",
    ]
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_native_acquisition_required"
        for event in events
    )


def test_failed_trailing_tool_with_missing_artifact_enters_recovery(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "pdf_extract" and len(requests) == 1:
            return (
                "pdf_extract",
                {
                    "output": (
                        "Resolved requested values by coordinate join: "
                        "Qwen3-VL-A22B-Instruct | RefCOCO-avg=91.9 | ERQA=51.3"
                    ),
                    "exit_code": 0,
                },
            )
        if block.tool_type == "pdf_extract":
            return (
                "pdf_extract",
                {
                    "output": "TooLarge: response body is over the hard cap",
                    "exit_code": 1,
                },
            )
        if block.tool_type == "write_file":
            return (
                "write_file",
                {
                    "output": "wrote /workspace/meta_analysis.csv",
                    "exit_code": 0,
                },
            )
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if recovering:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "write-1",
                    "name": "write_file",
                    "arguments": json.dumps({
                        "path": "/workspace/meta_analysis.csv",
                        "content": (
                            "Model,RefCOCO-avg,ERQA\n"
                            "Qwen3-VL-A22B-Instruct,91.9,51.3\n"
                        ),
                    }),
                }],
            }) + "\n\n"
        elif len(requests) == 1:
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "pdf-1",
                    "name": "pdf_extract",
                    "arguments": json.dumps({
                        "url": "https://arxiv.org/pdf/2511.21631",
                        "query": "Qwen3 RefCOCO ERQA",
                    }),
                }],
            }) + "\n\n"
        else:
            yield "data: " + json.dumps({
                "delta": (
                    "I found the needed values from the PDF evidence. The extracted "
                    "table evidence gives Qwen3-VL-A22B-Instruct RefCOCO-avg=91.9 "
                    "and ERQA=51.3. I also have enough structured information to "
                    "create the requested CSV artifact. However, I am going to make "
                    "one trailing verification call that may fail because the PDF is "
                    "large. If that trailing call fails, the harness must not stop "
                    "with this prose-only answer while the required artifact is still "
                    "missing; it must force artifact recovery and require a write_file "
                    "mutation. This intentionally mirrors the live benchmark failure "
                    "where a substantial answer was followed by a failed pdf_extract "
                    "attempt and no /workspace/meta_analysis.csv was produced."
                ),
            }) + "\n\n"
            yield "data: " + json.dumps({
                "type": "tool_calls",
                "calls": [{
                    "id": "pdf-2",
                    "name": "pdf_extract",
                    "arguments": json.dumps({
                        "url": "https://arxiv.org/pdf/2505.07062",
                        "query": "ERQA all models",
                    }),
                }],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{
            "role": "user",
            "content": "Create /workspace/meta_analysis.csv from the PDF evidence.",
        }],
        max_rounds=4,
        relevant_tools={"pdf_extract", "write_file"},
        forced_tools={"pdf_extract", "write_file"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/workspace/meta_analysis.csv"],
            },
        },
    )))

    assert [block.tool_type for block in executed[:3]] == [
        "pdf_extract",
        "pdf_extract",
        "write_file",
    ]
    assert any(block.tool_type == "write_file" for block in executed)
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "failed_trailing_tool_missing_artifacts"
        for event in events
    )


def test_failed_trailing_tool_after_explicit_continue_intent_gets_repair_round(monkeypatch):
    _patch_common(monkeypatch)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if len(executed) == 1:
            return block.tool_type, {
                "output": "output path must stay inside the active workspace",
                "exit_code": 1,
            }
        return block.tool_type, {
            "output": "exported /workspace/frame.png",
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            yield "data: " + json.dumps({
                "delta": (
                    "The initial inspection found several useful visual details. "
                    "The opening frame establishes the setting, while later frames "
                    "show distinct people, objects, and readable title elements. "
                    "Some details remain too small to distinguish confidently, and "
                    "guessing from the broad composition would be unreliable. "
                    "These observations are preliminary and need a closer view before "
                    "I can give a reliable answer. I will now export a detailed frame "
                    "inside a temporary location and inspect it more carefully."
                ),
            }) + "\n\n"
            output_path = "/tmp/frame.png"
        elif len(requests) == 2:
            output_path = "/workspace/frame.png"
        else:
            yield "data: " + json.dumps({
                "delta": "The closer inspection is complete; here is the final answer.",
            }) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"inspect-{len(requests)}",
                "name": "inspect_media",
                "arguments": json.dumps({
                    "path": "/workspace/source.mp4",
                    "timestamp": "00:00:01.000",
                    "output_path": output_path,
                }),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the supplied video and answer carefully."}],
        max_rounds=4,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
        },
    )))

    assert len(executed) == 2
    assert len(requests) == 3
    assert not any(event.get("type") == "rounds_exhausted" for event in events)


def test_complete_answer_still_survives_failed_illustrative_trailing_tool(monkeypatch):
    _patch_common(monkeypatch)
    requests = []

    async def _fake_exec(block, *args, **kwargs):
        return block.tool_type, {
            "output": "optional illustration could not be generated",
            "exit_code": 1,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        yield "data: " + json.dumps({
            "delta": (
                "The evidence supports a complete answer with three independent "
                "observations. First, the visible heading identifies the subject. "
                "Second, the surrounding labels establish the relevant context. "
                "Third, the final frame confirms the same interpretation from a "
                "different angle. Taken together, those details resolve the question "
                "without relying on the optional illustration. The conclusion is "
                "therefore supported directly by the inspected source."
            ),
        }) + "\n\n"
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": "optional-image",
                "name": "inspect_media",
                "arguments": json.dumps({
                    "path": "/workspace/source.mp4",
                    "output_path": "/tmp/optional.png",
                }),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Inspect the supplied video and answer carefully."}],
        max_rounds=4,
        relevant_tools={"inspect_media"},
        forced_tools={"inspect_media"},
        workspace="/workspace",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
        },
    ))

    assert len(requests) == 1


def test_failed_mutation_with_missing_artifact_recovers_before_failure_breaker(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(al, "_failed_tool_round_limit", lambda *args, **kwargs: 1, raising=False)
    requests = []
    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        if block.tool_type == "write_file":
            return "write_file", {
                "output": "wrote /workspace/output.png",
                "exit_code": 0,
            }
        return block.tool_type, {
            "output": "Traceback: chart generation failed",
            "exit_code": 1,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(messages)
        recovering = any(
            "Artifact recovery mode is active" in str(message.get("content") or "")
            for message in messages
        )
        if recovering:
            tool_text = (
                "```write_file\n"
                "/workspace/output.png\n"
                "minimal recovered artifact\n"
                "```"
            )
        else:
            tool_text = "```python\nraise RuntimeError('chart generation failed')\n```"
        yield "data: " + json.dumps({"delta": tool_text}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://127.0.0.1:18080/v1",
        "qwen35-9b-policy-test",
        [{"role": "user", "content": "Create /workspace/output.png."}],
        max_rounds=10,
        relevant_tools={"python", "write_file"},
        forced_tools={"python", "write_file"},
        workspace="/workspace",
        owner="admin",
        force_textual_tool_transport=True,
        client_runtime_context={
            "surface": "odysseus-native",
            "terminal_agent": True,
            "artifact_recovery_enabled": True,
            "completion_requirements": {
                "required_artifacts": ["/workspace/output.png"],
            },
        },
    )))

    assert any(block.tool_type == "write_file" for block in executed)
    assert any(
        event.get("type") == "completion_blocked"
        and event.get("reason") == "artifact_recovery_after_tool_failures"
        for event in events
    )
    assert not any(
        event.get("type") == "loop_breaker_triggered"
        and event.get("reason") == "consecutive_tool_failures"
        for event in events
    )


def test_csv_write_uses_resolved_pdf_extract_value_locks():
    blocks = al._normalize_required_artifact_write_paths(
        [
            al.ToolBlock(
                "write_file",
                (
                    "/workspace/papers/meta_analysis.csv\n"
                    "Model,RefCOCO-avg,ERQA\n"
                    "Qwen3-VL-A22B-Instruct,89.5,49.2\n"
                    "GLM-4-6V,88.6,56.2\n"
                    "Seed-1.5-VL-Thinking,91.3,44.1\n"
                ),
            )
        ],
        ["/workspace/meta_analysis.csv"],
        [
            {
                "tool": "pdf_extract",
                "output": (
                    'Resolved values JSON: {"model": "Qwen3-VL-A22B-Instruct", '
                    '"source": "pdf_extract_positioned_table", '
                    '"values": {"ERQA": "51.3", "RefCOCO": "91.9"}}\n'
                    'Resolved values JSON: {"model": "GLM-4.6V", '
                    '"source": "pdf_extract_positioned_table", '
                    '"values": {"ERQA": "47.8", "RefCOCO": "88.6"}}\n'
                    'Resolved values JSON: {"model": "Seed-1.5-VL-Thinking", '
                    '"source": "pdf_extract_positioned_table", '
                    '"values": {"ERQA": null, "RefCOCO": "91.3"}}\n'
                    "Resolved requested values by coordinate join: "
                    "Qwen3-VL-A22B-Instruct | RefCOCO=91.9 | ERQA=51.3\n"
                    "Resolved requested values by coordinate join: "
                    "GLM-4.6V | RefCOCO=88.6 | ERQA=47.8\n"
                    "Resolved requested values by coordinate join: "
                    "Seed-1.5-VL-Thinking | RefCOCO=91.3 | "
                    "requested metrics not found: ERQA\n"
                ),
            }
        ],
    )

    assert blocks == [
        al.ToolBlock(
            "write_file",
            (
                "/workspace/meta_analysis.csv\n"
                "Model,RefCOCO-avg,ERQA\n"
                "Qwen3-VL-A22B-Instruct,91.9,51.3\n"
                "GLM-4.6V,88.6,47.8\n"
                "Seed-1.5-VL-Thinking,91.3,N/A"
            ),
        )
    ]


def test_eval_varied_read_only_inspections_converge_before_round_cap(monkeypatch):
    _patch_common(monkeypatch)
    rounds = []

    async def _fake_exec(block, *args, **kwargs):
        return ("host_shell", {"output": f"status round {len(rounds)}", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        n = len(rounds)
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"read-only-{n}",
                "name": "host_shell",
                "arguments": json.dumps({"command": f"pwd && ls -la probe-{n}"}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "_tool_result_signature", lambda records: repr(records))

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Fix the parser and run its test."}],
        max_rounds=12,
        relevant_tools={"host_shell"},
        workspace="/home/tester/project",
        owner="admin",
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
            "local_capability_contract": {
                "routing": {"local_workspace_first": True},
            },
        },
    )))

    guard = next(e for e in events if e.get("type") == "loop_breaker_triggered")
    assert guard["detail"] == "repeated read-only inspections without a mutation or new answer"
    assert len(rounds) < 12


def test_web_fetch_pagination_signature_ignores_page_args():
    sig1 = al._web_fetch_pagination_signature({
        "tool_name": "web_fetch",
        "content": json.dumps({
            "url": "https://crates.io/api/v1/crates?sort=downloads&per_page=30&page=1",
        }),
        "result": {"output": "ok", "exit_code": 0},
    })
    sig2 = al._web_fetch_pagination_signature({
        "tool_name": "web_fetch",
        "content": json.dumps({
            "url": "https://crates.io/api/v1/crates?page=8&per_page=5&sort=downloads",
        }),
        "result": {"output": "ok", "exit_code": 0},
    })

    assert sig1
    assert sig1 == sig2


def test_repeated_web_fetch_pagination_forces_convergence(monkeypatch):
    _patch_common(monkeypatch)
    rounds = []

    async def _fake_exec(block, *args, **kwargs):
        args = json.loads(block.content)
        page = args["url"].split("page=", 1)[-1]
        return ("Fetch webpage", {
            "output": f'{{"crates":[{{"name":"crate-{page}","downloads":1000}}]}}',
            "exit_code": 0,
        })

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        n = len(rounds)
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"fetch-page-{n}",
                "name": "web_fetch",
                "arguments": json.dumps({
                    "url": (
                        "https://crates.io/api/v1/crates"
                        f"?sort=downloads&per_page=30&page={n}"
                    ),
                }),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "How many crates have over one billion downloads?"}],
        max_rounds=10,
        relevant_tools={"web_fetch"},
    )))

    guard = next(e for e in events if e.get("type") == "loop_breaker_triggered")
    assert guard["reason"] == "repeated_web_pagination"
    assert len(rounds) < 10


def test_approval_continuation_cannot_fallback_into_host_shell(monkeypatch):
    _patch_common(monkeypatch)
    content = json.dumps({
        "folder": "INBOX",
        "max_results": 1,
        "unread_only": False,
    })
    pending = tool_approval_store.create(
        owner="admin",
        session_id="approval-fallback-test",
        origin_run_id="approval-fallback-run",
        tool_name="mcp__email__list_emails",
        content=content,
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("mcp__email__list_emails", content),
        request_text="What's my latest email?",
    )
    grant = tool_approval_store.consume(
        pending.approval_id,
        decision="approve",
        owner="admin",
        session_id="approval-fallback-test",
    )
    assert grant is not None
    seen_tools = []

    async def _fake_exec(block, *args, **kwargs):
        seen_tools.append(block.tool_type)
        return (block.tool_type, {"error": "disabled by user", "exit_code": 1})

    async def _fake_stream(_candidates, messages, **kwargs):
        yield "data: {\"delta\":\"\"}\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Approved the exact action once."}],
        max_rounds=2,
        relevant_tools={"mcp__email__list_emails", "host_shell"},
        owner="admin",
        session_id="approval-fallback-test",
        exact_approval=grant,
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {"local_workspace_tasks": "use_host_shell_bridge"},
        },
    )))

    assert seen_tools == ["mcp__email__list_emails"]
    assert not any(
        event.get("type") == "tool_start" and event.get("tool") == "host_shell"
        for event in events
    )
    final = next(event for event in events if event.get("type") == "final_response")
    assert "disabled by user" in final["content"]


def test_eval_local_network_tool_budget_forces_final_synthesis(monkeypatch):
    _patch_common(monkeypatch)
    rounds = []

    async def _fake_exec(block, *args, **kwargs):
        return ("host_shell", {"output": f"new evidence {block.content}", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds.append(messages)
        n = len(rounds)
        yield "data: " + json.dumps({
            "type": "tool_calls",
            "calls": [{
                "id": f"network-call-{n}",
                "name": "host_shell",
                "arguments": json.dumps({"command": f"network probe {n}"}),
            }],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "_tool_result_signature", lambda records: repr(records))

    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": "Find the local IP and SSH route to ajax."}],
        max_rounds=20,
        relevant_tools={"host_shell"},
        workspace=None,
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/home/tester/project",
            "host_shell_bridge": {"url": "http://host.docker.internal:17654/run", "token": "x"},
            "runtime_execution_contract": {
                "local_workspace_tasks": "use_host_shell_bridge",
            },
        },
    )))

    guard = next(e for e in events if e.get("type") == "loop_breaker_triggered")
    assert guard["reason"] == "local_network_tool_budget"
    assert len(rounds) < 20
