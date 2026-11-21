"""Tests for issue #7278: preserve and replay sibling tool calls on approval continuation."""

import asyncio
import json
from dataclasses import replace

import pytest

import src.agent_loop as al
import src.tool_capabilities as tool_capabilities
from src.tool_approvals import ExactToolApproval, ToolApprovalStore, tool_approval_store
from src.tool_capabilities import ToolGateDecision, capabilities_for_action


@pytest.fixture(autouse=True)
def _enable_approval_gate(monkeypatch):
    monkeypatch.setattr(tool_capabilities, "TOOL_APPROVAL_GATE_ENABLED", True)


def _collect(gen):
    async def _run():
        return [c async for c in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_loop(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    import src.context_compactor as context_compactor

    monkeypatch.setattr(context_compactor, "get_context_length", lambda *a, **k: 128_000)

    async def _unconfigured_direct_provider(*args, **kwargs):
        raise RuntimeError("No direct-provider completion configured in this fixture")
        yield

    monkeypatch.setattr(al, "stream_llm", _unconfigured_direct_provider)
    monkeypatch.setattr(al, "tool_result_should_arm_gate", lambda *a, **k: False, raising=False)


def test_pending_sibling_calls_are_canonical_bounded_hidden_and_digest_bound():
    store = ToolApprovalStore()
    raw_siblings = [
        ("manage_notes", '{"action": "search", "query": "alpha"}'),
        ("", "ignored"),
        ("x" * 600, "too-long-name"),
    ]
    raw_siblings.extend((f"tool_{idx}", f"payload-{idx}") for idx in range(30))

    pending = store.create(
        owner="Alice",
        session_id="session-7278",
        origin_run_id="run-7278",
        tool_name="manage_notes",
        content='{"action": "add", "title": "First", "content": "Hello"}',
        workspace=None,
        external_untrusted_context_seen=True,
        selected_tools=["manage_notes"],
        continuation_query="save note and search existing notes",
        pending_sibling_calls=raw_siblings,
        capabilities=capabilities_for_action(
            "manage_notes",
            '{"action": "add", "title": "First", "content": "Hello"}',
        ),
    )

    assert len(pending.pending_sibling_calls) == 16
    assert pending.pending_sibling_calls[0] == (
        "manage_notes",
        '{"action": "search", "query": "alpha"}',
    )
    assert all(len(name) <= 512 for name, _ in pending.pending_sibling_calls)

    payload = pending.public_payload()
    serialized = json.dumps(payload, sort_keys=True)
    assert "pending_sibling_calls" not in serialized
    assert "alpha" not in serialized

    tampered = replace(
        pending,
        pending_sibling_calls=(
            ("manage_notes", '{"action": "delete", "id": "secret"}'),
        ),
    )
    grant = ExactToolApproval(tampered)
    assert (
        grant.matches(
            owner="alice",
            session_id="session-7278",
            tool_name="manage_notes",
            content='{"action": "add", "title": "First", "content": "Hello"}',
            workspace=None,
        )
        is False
    )


def test_approval_continuation_replays_sibling_calls_instead_of_stopping_at_done(monkeypatch):
    _patch_loop(monkeypatch)
    executed_calls: list[tuple[str, str]] = []
    llm_rounds: list[int] = []

    def _gate_first_note_add(self, tool_name, content="", **kwargs):
        if (
            not self.approval_gate_bypassed
            and tool_name == "manage_notes"
            and '"add"' in str(content)
        ):
            return ToolGateDecision(
                False,
                reason="Note mutation requires approval.",
            )
        return ToolGateDecision(True)

    monkeypatch.setattr(
        al.ToolRunSecurityContext,
        "decision_for",
        _gate_first_note_add,
        raising=False,
    )

    async def _fake_exec(block, *a, **k):
        executed_calls.append((block.tool_type, block.content))
        if block.tool_type == "manage_notes" and '"add"' in block.content:
            return ("manage_notes: add", {"output": "Saved note First.", "exit_code": 0})
        if block.tool_type == "manage_notes" and '"search"' in block.content:
            return ("manage_notes: search", {"output": "Found note Beta.", "exit_code": 0})
        return (block.tool_type, {"output": "ok", "exit_code": 0})

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    proposal_round_output = (
        '<tool_call>{"name": "manage_notes", "arguments": {"action": "add", "title": "First", "content": "Hello"}}</tool_call>\n'
        '<tool_call>{"name": "manage_notes", "arguments": {"action": "search", "query": "Beta"}}</tool_call>'
    )

    async def _proposal_stream(_candidates, messages, **kwargs):
        llm_rounds.append(1)
        yield f'data: {json.dumps({"delta": proposal_round_output})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _proposal_stream, raising=False)

    proposal_events = _events(
        _collect(
            al.stream_agent_loop(
                "http://x/v1",
                "m",
                [{"role": "user", "content": "Save First and search Beta"}],
                session_id="session-7278-e2e",
                owner="alice",
                max_rounds=4,
                relevant_tools={"manage_notes"},
            )
        )
    )

    ask_events = [ev for ev in proposal_events if ev.get("type") == "ask_user"]
    assert len(ask_events) == 1
    approval_id = ask_events[0]["data"]["approval_id"]
    pending = tool_approval_store.peek(approval_id)
    assert pending is not None
    assert len(pending.pending_sibling_calls) == 1
    assert pending.pending_sibling_calls[0][0] == "manage_notes"
    assert '"search"' in pending.pending_sibling_calls[0][1]
    assert executed_calls == []

    grant = tool_approval_store.consume(
        approval_id,
        decision="approve_task",
        owner="alice",
        session_id="session-7278-e2e",
    )
    assert grant is not None

    async def _continuation_stream(_candidates, messages, **kwargs):
        llm_rounds.append(2)
        yield f'data: {json.dumps({"delta": "Added note First and found Beta."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _continuation_stream, raising=False)

    continuation_events = _events(
        _collect(
            al.stream_agent_loop(
                "http://x/v1",
                "m",
                [{"role": "user", "content": "Save First and search Beta"}],
                session_id="session-7278-e2e",
                owner="alice",
                max_rounds=4,
                relevant_tools={"manage_notes"},
                exact_approval=grant,
            )
        )
    )

    assert len(executed_calls) == 2
    assert '"add"' in executed_calls[0][1]
    assert '"search"' in executed_calls[1][1]
    assert llm_rounds == [1, 2]

    streamed_text = "".join(
        ev.get("delta", "") for ev in continuation_events if isinstance(ev.get("delta"), str)
    )
    final_events = [ev for ev in continuation_events if ev.get("type") == "final_response"]
    combined_output = final_events[-1]["content"] if final_events else streamed_text
    assert "Added note First and found Beta." in combined_output
    assert all(ev.get("content") != "Done." for ev in final_events)
