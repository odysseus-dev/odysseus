from __future__ import annotations

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from mcp_servers.odysseus_server import (  # noqa: E402
    REQUIRED_OPERATIONS,
    OdysseusMcpServer,
)
from services.agents.approvals import ApprovalAuthority, ConfirmedAction  # noqa: E402
from services.agents.delegation import (  # noqa: E402
    DelegationAuthority,
    DelegationDenied,
    WorkloadAuthenticationRequired,
)
from services.agents.approvals import ApprovalDenied  # noqa: E402


class Workload:
    def authenticate(self) -> str:
        return "workload-1"


@pytest.fixture
def stack():
    delegation = DelegationAuthority(hmac_key=b"mcp-delegation-key-32-bytes!!")
    approvals = ApprovalAuthority(hmac_key=b"mcp-approval-key-32-bytes!!!!")
    server = OdysseusMcpServer(delegation=delegation, approvals=approvals)
    token = delegation.issue(
        audience="odysseus-mcp",
        execution_id="exec-1",
        conversation_id="conv-1",
        owner="user-1",
        scopes={
            "notes.read",
            "notes.write",
            "documents.read",
            "documents.index",
            "mail.read",
            "mail.send",
            "calendar.read",
            "calendar.write",
            "memory.read",
            "memory.write",
            "research.invoke",
            "tasks.read",
            "tasks.write",
            "sessions.read",
            "gallery.read",
            "notifications.read",
            "skills.read",
            "skills.invoke",
        },
        resources={"note:1", "mailbox:inbox", "calendar:primary"},
        profile_id="odysseus",
        profile_revision=1,
        archetype_id="chat",
        archetype_version=1,
        budget=100,
        max_depth=0,
        ttl_seconds=60,
    )
    return server, token, approvals


@pytest.mark.parametrize("operation", REQUIRED_OPERATIONS)
def test_mcp_operation_calls_domain_service_not_route(operation, stack):
    server, _, _ = stack
    assert server.registry[operation].target_layer == "service"


def test_missing_workload_is_denied(stack):
    server, token, _ = stack
    with pytest.raises(WorkloadAuthenticationRequired):
        server.call("notes.read", workload=None, token=token, arguments={})


def test_missing_token_is_denied(stack):
    server, _, _ = stack
    with pytest.raises(DelegationDenied):
        server.call("notes.read", workload=Workload(), token=None, arguments={})


def test_missing_scope_is_denied(stack):
    server, token, _ = stack
    narrow = server.delegation.issue(
        audience="odysseus-mcp",
        execution_id="exec-1",
        conversation_id="conv-1",
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
    with pytest.raises(DelegationDenied, match="scope"):
        server.call("mail.send", workload=Workload(), token=narrow, arguments={"to": ["a@example.test"]})


def test_missing_resource_grant_is_denied(stack):
    server, token, _ = stack
    with pytest.raises(DelegationDenied, match="resource"):
        server.call(
            "notes.read",
            workload=Workload(),
            token=token,
            arguments={"resource": "note:999"},
        )


def test_mutating_tool_requires_approval_grant(stack):
    server, token, _ = stack
    with pytest.raises(ApprovalDenied):
        server.call(
            "mail.send",
            workload=Workload(),
            token=token,
            arguments={"to": ["a@example.test"], "body": "x", "resource": "mailbox:inbox"},
            idempotency_key="mail-1",
        )


def test_approved_mail_send_hits_service_once(stack):
    server, token, approvals = stack
    action = ConfirmedAction(
        event_id="action-1",
        execution_id="exec-1",
        conversation_id="conv-1",
        tool="mail.send",
        arguments={"to": ["a@example.test"], "body": "x", "resource": "mailbox:inbox"},
        owner="user-1",
        profile_id="odysseus",
        profile_revision=1,
        resources=frozenset({"mailbox:inbox"}),
    )
    grant = approvals.issue_from_confirmation(action)
    first = server.call(
        "mail.send",
        workload=Workload(),
        token=token,
        grant=grant,
        arguments=action.arguments,
        idempotency_key="mail-1",
    )
    second = server.call(
        "mail.send",
        workload=Workload(),
        token=token,
        grant=grant,
        arguments=action.arguments,
        idempotency_key="mail-1",
    )
    assert first == second
    assert server.services["mail"].sent == [
        {"owner": "user-1", "to": ["a@example.test"], "body": "x", "resource": "mailbox:inbox"}
    ]
