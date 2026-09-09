from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.approvals import ApprovalAuthority, ConfirmedAction  # noqa: E402
from services.agents.dispatcher import AgentDispatcher  # noqa: E402
from services.agents.projection import ProjectionReconciler, ProjectionStore, SourceEvent  # noqa: E402
from mcp_servers.odysseus_server import OdysseusMcpServer, REQUIRED_OPERATIONS  # noqa: E402


class FakeClient:
    def create_or_resume(self, **kwargs):
        return type("R", (), {"conversation_id": "conv-acc", "execution_id": "agent-server:conv-acc"})()


def test_in_process_platform_acceptance_bundle():
    dispatcher = AgentDispatcher(client=FakeClient())
    ref = dispatcher.dispatch(request_id="acc-1", archetype="chat", payload={"text": "hi"})
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.reconcile(
        [
            SourceEvent(
                source_system="agent-server",
                source_event_id="e1",
                kind="MessageEvent",
                conversation_id=ref.conversation_id,
                execution_id=ref.automation_execution_id,
                parent_id=None,
                source_order=1,
                payload={"text": "hi"},
            )
        ]
    )
    from services.agents.delegation import DelegationAuthority

    approvals = ApprovalAuthority(hmac_key=b"acc-approval-key-32-bytes!!!!!!")
    server = OdysseusMcpServer(
        delegation=DelegationAuthority(hmac_key=b"acc-delegation-key-32-bytes!!!"),
        approvals=approvals,
    )
    action = ConfirmedAction(
        event_id="a1",
        execution_id=ref.automation_execution_id,
        conversation_id=ref.conversation_id,
        tool="mail.send",
        arguments={"to": ["a@example.test"]},
        owner="user-1",
        profile_id="odysseus",
        profile_revision=1,
        resources=frozenset(),
    )
    grant = approvals.issue_from_confirmation(action)
    approvals.verify_and_consume(grant, action)
    snap = reconciler.snapshot()
    assert ref.conversation_id == "conv-acc"
    assert snap.conversation_id == "conv-acc"
    assert set(REQUIRED_OPERATIONS) <= set(server.registry)
    assert grant.signature


def test_live_docker_tailscale_acceptance_is_evidence_gated():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("openhands_probe", root / "scripts" / "openhands_probe.py")
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    result = probe.probe_acceptance()
    assert result.name == "acceptance"
    assert "live_stack" in result.evidence
    assert result.evidence["in_process_gates"] is True
