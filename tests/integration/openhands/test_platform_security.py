from __future__ import annotations

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.approvals import ApprovalAuthority, ApprovalDenied, ConfirmedAction  # noqa: E402
from services.agents.delegation import DelegationAuthority, DelegationDenied  # noqa: E402
from services.agents.dispatcher import AgentDispatcher  # noqa: E402


def test_child_cannot_escalate_scope():
    authority = DelegationAuthority(hmac_key=b"sec-delegation-key-32-bytes!!!!")
    parent = authority.issue(
        audience="odysseus-mcp",
        execution_id="exec-1",
        conversation_id="c1",
        owner="user-1",
        scopes={"notes.read"},
        resources={"note:1"},
        profile_id="odysseus",
        profile_revision=1,
        archetype_id="chat",
        archetype_version=1,
        budget=10,
        max_depth=1,
        ttl_seconds=60,
        allowed_child_archetypes={"chat"},
    )
    with pytest.raises(DelegationDenied):
        authority.issue_child(parent, scopes={"notes.read", "mail.send"})


def test_cross_execution_token_is_denied():
    authority = DelegationAuthority(hmac_key=b"sec-delegation-key-32-bytes!!!!")
    token = authority.issue(
        audience="odysseus-mcp",
        execution_id="exec-1",
        conversation_id="c1",
        owner="user-1",
        scopes={"notes.read"},
        resources=set(),
        profile_id="odysseus",
        profile_revision=1,
        archetype_id="chat",
        archetype_version=1,
        budget=10,
        max_depth=0,
        ttl_seconds=60,
    )
    with pytest.raises(DelegationDenied, match="execution"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-other")


def test_replayed_and_altered_grants_are_denied():
    approvals = ApprovalAuthority(hmac_key=b"sec-approval-key-32-bytes!!!!!!")
    action = ConfirmedAction(
        event_id="a1",
        execution_id="exec-1",
        conversation_id="c1",
        tool="mail.send",
        arguments={"to": ["a@example.test"]},
        owner="user-1",
        profile_id="odysseus",
        profile_revision=1,
        resources=frozenset(),
    )
    grant = approvals.issue_from_confirmation(action)
    approvals.verify_and_consume(grant, action)
    with pytest.raises(ApprovalDenied):
        approvals.verify_and_consume(grant, action)
    with pytest.raises(ApprovalDenied):
        approvals.verify_and_consume(grant, action.with_args(to=["b@example.test"]))


def test_duplicate_dispatch_is_idempotent():
    class Client:
        def create_or_resume(self, **kwargs):
            return type("R", (), {"conversation_id": "c1", "execution_id": "agent-server:c1"})()

    dispatcher = AgentDispatcher(client=Client())
    first = dispatcher.dispatch(request_id="dup", archetype="chat", payload={"text": "hi"})
    second = dispatcher.dispatch(request_id="dup", archetype="chat", payload={"text": "hi"})
    assert first == second
