"""Task-scoped exact approval continuation coverage for issue #6112."""

import json
from dataclasses import replace
from pathlib import Path

from routes.chat_helpers import _without_latest_matching_user_message
from src.tool_approvals import ExactToolApproval, ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action


def _pending(
    store: ToolApprovalStore,
    *,
    selected_tools=None,
    continuation_query="inspect the project using memory and skills",
):
    content = "printf exact"
    return store.create(
        owner="Alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content=content,
        workspace=None,
        external_untrusted_context_seen=True,
        selected_tools=selected_tools,
        continuation_query=continuation_query,
        capabilities=capabilities_for_action("bash", content),
    )


def test_card_offers_once_task_and_deny_without_leaking_continuation_state():
    pending = _pending(
        ToolApprovalStore(),
        selected_tools=["manage_skills", "bash", "manage_skills"],
    )

    payload = pending.public_payload()

    assert [option["value"] for option in payload["options"]] == [
        "approve",
        "approve_task",
        "deny",
    ]
    assert [option["label"] for option in payload["options"]] == [
        "Allow once",
        "Allow for this task",
        "Deny",
    ]
    serialized = json.dumps(payload, sort_keys=True)
    assert "selected_tools" not in serialized
    assert "continuation_query" not in serialized
    assert "manage_skills" not in serialized
    assert "inspect the project" not in serialized


def test_allow_for_task_bypasses_only_the_resumed_run_gate():
    store = ToolApprovalStore()
    pending = _pending(store, selected_tools=["bash", "manage_skills"])
    grant = store.consume(
        pending.approval_id,
        decision="approve_task",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert grant.allow_remaining_actions is True
    assert grant.pending.continuation_query == (
        "inspect the project using memory and skills"
    )

    resumed = ToolRunSecurityContext(
        external_untrusted_context_seen=True,
        approval_gate_bypassed=grant.allow_remaining_actions,
    )
    assert resumed.decision_for("bash").allowed is True

    # A new ordinary user turn constructs a fresh context and asks again.
    fresh = ToolRunSecurityContext(external_untrusted_context_seen=True)
    assert fresh.decision_for("bash").allowed is False


def test_allow_once_restores_context_and_candidates_but_keeps_exact_gate():
    store = ToolApprovalStore()
    pending = _pending(store, selected_tools=["bash", "manage_skills"])
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert grant.allow_remaining_actions is False
    assert grant.pending.selected_tools == ("bash", "manage_skills")
    assert grant.pending.continuation_query.startswith("inspect the project")
    context = ToolRunSecurityContext(
        external_untrusted_context_seen=True,
        approval_gate_bypassed=grant.allow_remaining_actions,
    )
    assert context.decision_for("bash").allowed is False


def test_private_continuation_state_is_canonical_bounded_and_digest_bound():
    selected_tools = ["manage_skills", "bash", "manage_skills", "", 7]
    selected_tools.extend(f"tool_{index:04d}" for index in range(600))
    selected_tools.append("x" * 513)
    pending = _pending(
        ToolApprovalStore(),
        selected_tools=selected_tools,
        continuation_query="  " + ("original request " * 500),
    )
    assert pending.selected_tools[:2] == ("bash", "manage_skills")
    assert len(pending.selected_tools) == 512
    assert all(len(name) <= 512 for name in pending.selected_tools)
    assert "x" * 513 not in pending.selected_tools
    assert pending.continuation_query.startswith("original request")
    assert len(pending.continuation_query) == 4000

    tampered = replace(
        pending,
        selected_tools=("bash", "manage_skills", "send_email"),
        continuation_query="different request",
    )
    grant = ExactToolApproval(tampered)
    assert grant.matches(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace=None,
    ) is False


def test_route_context_agent_frontend_and_cache_bust_wire_the_contract():
    root = Path(__file__).resolve().parents[1]
    route = (root / "routes/chat_routes.py").read_text(encoding="utf-8")
    helpers = (root / "routes/chat_helpers.py").read_text(encoding="utf-8")
    agent = (root / "src/agent_loop.py").read_text(encoding="utf-8")
    frontend = (root / "static/js/chat.js").read_text(encoding="utf-8")
    index = (root / "static/index.html").read_text(encoding="utf-8")

    assert 'decision not in {"approve", "approve_task", "deny"}' in route
    assert "set(pending_tool_approval.selected_tools)" in route
    assert "pending_tool_approval.continuation_query" in route
    assert "exclude_current_user_from_context=bool(exact_tool_approval)" in route
    assert "continuation_context_message: str | None = None" in helpers
    assert "_without_latest_matching_user_message(" in helpers
    assert "selected_tools=approval_selected_tools" in agent
    assert "continuation_query=_retrieval_query or _last_user" in agent
    assert "approval_gate_bypassed=bool(" in agent
    assert "['approve', 'approve_task', 'deny']" in frontend
    assert "chat.js?v=20260819approvaltask1" in index


def test_context_helper_removes_only_the_newest_matching_user_event():
    messages = [
        {"role": "user", "content": "original task"},
        {"role": "assistant", "content": "approval card"},
        {"role": "user", "content": "Allow once"},
        {"role": "user", "content": "different later data message"},
    ]

    filtered = _without_latest_matching_user_message(messages, "Allow once")

    assert messages[-2]["content"] == "Allow once"
    assert [item["content"] for item in filtered] == [
        "original task",
        "approval card",
        "different later data message",
    ]
