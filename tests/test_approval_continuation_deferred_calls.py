"""Regression for #7278: an approval card must not silently drop the rest of
the model's tool batch.

The card is a turn boundary, so calls after the gated one never run in the
original request. Before this fix, approving an effectful action (for example
the first of five ``manage_memory`` adds) ran only that action and ended the
continuation with a hard-coded "Done.", so the model never learned the other
calls were skipped and the user saw a false success.
"""

import asyncio
import json

import pytest

import src.agent_loop as al
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolGateDecision, capabilities_for_action


OWNER = "alice"
SESSION = "session-7278"
FIRST_ADD = "add\nWorks as a solutions architect.\nfact"


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                out.append(json.loads(chunk[6:]))
            except ValueError:
                pass
    return out


def _grant(deferred_tool_calls=None):
    store = ToolApprovalStore()
    pending = store.create(
        owner=OWNER,
        session_id=SESSION,
        origin_run_id="run-7278",
        tool_name="manage_memory",
        content=FIRST_ADD,
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("manage_memory", FIRST_ADD),
        selected_tools={"manage_memory"},
        request_text="Remember these facts about me, one add call per fact.",
        deferred_tool_calls=deferred_tool_calls,
    )
    grant = store.consume(
        pending.approval_id, decision="approve", owner=OWNER, session_id=SESSION
    )
    assert grant is not None
    return pending, grant


def _patch_loop(monkeypatch, model_rounds):
    """Keep the real loop body; fake the model, settings and tool dispatch."""
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    import src.context_compactor as context_compactor

    monkeypatch.setattr(context_compactor, "get_context_length", lambda *a, **k: 128_000)

    async def _no_direct_provider(*args, **kwargs):
        raise RuntimeError("No direct-provider completion configured in this fixture")
        yield  # pragma: no cover - keeps the async-generator interface

    monkeypatch.setattr(al, "stream_llm", _no_direct_provider)
    monkeypatch.setattr(al, "tool_result_should_arm_gate", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(
        al.ToolRunSecurityContext,
        "decision_for",
        lambda self, *a, **k: ToolGateDecision(True),
        raising=False,
    )

    executed = []

    async def _fake_exec(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        return (
            f"{block.tool_type}: add",
            {"output": f"Memory added: {block.content.splitlines()[1]}", "exit_code": 0},
        )

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    seen_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen_messages.append([dict(m) for m in messages if isinstance(m, dict)])
        text = model_rounds[min(len(seen_messages), len(model_rounds)) - 1]
        yield f"data: {json.dumps({'delta': text})}\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    return executed, seen_messages


def _run_continuation(grant):
    return _events(
        _collect(
            al.stream_agent_loop(
                "http://inference.invalid/v1",
                "test-model",
                [
                    {
                        "role": "user",
                        "content": "Remember these facts about me, one add call per fact.",
                    }
                ],
                owner=OWNER,
                session_id=SESSION,
                exact_approval=grant,
                relevant_tools={"manage_memory"},
                max_rounds=3,
            )
        )
    )


def _final_responses(events):
    return [e.get("content") for e in events if e.get("type") == "final_response"]


def test_pending_approval_keeps_deferred_calls_in_order_and_off_the_card():
    pending, _ = _grant(["manage_memory"] * 4 + ["", 7, "x" * 600, "manage_notes"])

    assert pending.deferred_tool_calls == ("manage_memory",) * 4 + ("manage_notes",)
    serialized = json.dumps(pending.public_payload(), sort_keys=True)
    assert "deferred" not in serialized


def test_pending_approval_without_deferred_calls_defaults_to_empty():
    pending, _ = _grant()

    assert pending.deferred_tool_calls == ()


def test_deferred_note_summarizes_counts_and_grants_nothing():
    note = al._deferred_approval_calls_note(["manage_memory"] * 4 + ["manage_notes"])

    assert "5 more tool calls" in note
    assert "manage_memory x4" in note
    assert "manage_notes" in note
    assert "did NOT run" in note
    assert al._deferred_approval_calls_note([]) == ""
    assert al._deferred_approval_calls_note(None) == ""


def test_single_action_approval_still_finishes_with_done(monkeypatch):
    executed, seen_messages = _patch_loop(monkeypatch, ["unexpected model round"])
    _, grant = _grant()

    events = _run_continuation(grant)

    assert executed == [("manage_memory", FIRST_ADD)]
    assert "Done." in _final_responses(events)
    assert seen_messages == []


def test_approval_with_deferred_calls_returns_to_the_model(monkeypatch):
    executed, seen_messages = _patch_loop(
        monkeypatch, ["The first fact is saved; re-issuing the remaining four."]
    )
    _, grant = _grant(["manage_memory"] * 4)

    events = _run_continuation(grant)

    # The sealed action still runs exactly once, and nothing else runs
    # without the model asking again: the deferred calls are not replayed.
    assert executed == [("manage_memory", FIRST_ADD)]
    assert "Done." not in _final_responses(events)
    assert seen_messages, "the model must get a round after the approved action"
    transcript = "\n".join(str(m.get("content") or "") for m in seen_messages[0])
    assert "Memory added: Works as a solutions architect." in transcript
    assert "4 more tool calls (manage_memory x4) that did NOT run" in transcript


def test_failed_approved_action_keeps_reporting_the_failure(monkeypatch):
    _, seen_messages = _patch_loop(monkeypatch, ["unexpected model round"])

    async def _failing_exec(block, *args, **kwargs):
        return (f"{block.tool_type}: BLOCKED", {"error": "disabled", "exit_code": 1, "blocked": True})

    monkeypatch.setattr(al, "execute_tool_block", _failing_exec, raising=False)
    _, grant = _grant(["manage_memory"] * 4)

    events = _run_continuation(grant)

    finals = _final_responses(events)
    assert any("could not run" in (text or "") for text in finals)
    assert seen_messages == []
