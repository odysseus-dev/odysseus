"""Callers with no resumable chat keep the original one-use approval scope.

The chat card reuses the wire value ``approve`` for chat-session scope, so any
caller that still sends ``approve`` meaning "once" has to say so explicitly or
it silently inherits a run-long gate bypass.
"""

from pathlib import Path

from src.tool_approval_scopes import ToolApprovalScope
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action


def _pending(store: ToolApprovalStore, *, session_id=""):
    content = "printf exact"
    return store.create(
        owner="Alice",
        session_id=session_id,
        origin_run_id="run-1",
        tool_name="bash",
        content=content,
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", content),
    )


def test_single_action_grant_leaves_the_gate_armed_behind_the_sealed_action():
    store = ToolApprovalStore()
    pending = _pending(store)

    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id=None,
        allow_continuation=False,
    )

    assert grant is not None
    assert grant.scope is ToolApprovalScope.SINGLE_ACTION
    assert grant.allow_remaining_actions is False
    assert grant.grants_chat_session is False

    resumed = ToolRunSecurityContext(
        external_untrusted_context_seen=True,
        approval_gate_bypassed=grant.allow_remaining_actions,
    )
    assert resumed.decision_for("bash").allowed is False


def test_chat_callers_still_get_the_continuation_scope_they_asked_for():
    store = ToolApprovalStore()
    pending = _pending(store, session_id="session-1")

    grant = store.consume(
        pending.approval_id,
        decision="approve_task",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert grant.scope is ToolApprovalScope.TASK
    assert grant.allow_remaining_actions is True


def test_deny_is_unaffected_by_the_single_action_flag():
    store = ToolApprovalStore()
    pending = _pending(store)

    assert store.consume(
        pending.approval_id,
        decision="deny",
        owner="alice",
        session_id=None,
        allow_continuation=False,
    ) is None
    assert store.peek(pending.approval_id) is None


def test_skill_test_approval_route_opts_out_of_continuation():
    root = Path(__file__).resolve().parents[1]
    skills = (root / "routes/skills_routes.py").read_text(encoding="utf-8")

    approve_call = skills.index("exact_approval = tool_approval_store.consume(")
    end = skills.index(")", skills.index("allow_continuation", approve_call))
    assert "allow_continuation=False" in skills[approve_call:end]
