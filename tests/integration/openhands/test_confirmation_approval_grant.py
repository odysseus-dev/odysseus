from pathlib import Path
import importlib.util
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "openhands_probe", ROOT / "scripts/openhands_probe.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe():
    return _load_probe()


@pytest.fixture
def stack(probe):
    return probe.approval_grant_stack()


def test_mcp_side_effect_waits_for_odysseus_grant(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    assert pending.event_id
    assert pending.source == "ActionEvent"
    assert stack.domain_calls("mail.send") == []
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    stack.resume_with_grant(run, grant)
    assert len(stack.domain_calls("mail.send")) == 1


def test_altered_arguments_reject_grant(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    with pytest.raises(stack.ApprovalDenied):
        stack.resume_with_grant(
            run, grant, arguments={"to": ["b@example.test"], "body": "x"}
        )
    assert stack.domain_calls("mail.send") == []


def test_expired_grant_is_rejected(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id, ttl_seconds=-1)
    with pytest.raises(stack.ApprovalDenied):
        stack.resume_with_grant(run, grant)
    assert stack.domain_calls("mail.send") == []


def test_replayed_grant_is_rejected(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    stack.resume_with_grant(run, grant)
    with pytest.raises(stack.ApprovalDenied):
        stack.resume_with_grant(run, grant)
    assert len(stack.domain_calls("mail.send")) == 1


def test_cross_execution_grant_is_rejected(stack):
    first = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(first)
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    second = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    with pytest.raises(stack.ApprovalDenied):
        stack.resume_with_grant(second, grant)
    assert stack.domain_calls("mail.send") == []


def test_rejected_confirmation_never_invokes_mcp(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    stack.reject(pending.event_id)
    with pytest.raises(stack.ApprovalDenied):
        stack.issue_grant(pending.event_id)
    assert stack.domain_calls("mail.send") == []


def test_pending_action_evidence_fields(probe):
    evidence = probe.PendingActionEvidence(
        event_id="evt-1",
        conversation_id="c1",
        execution_id="e1",
        tool="mail.send",
        arguments={"to": ["a@example.test"]},
        source="ActionEvent",
    )
    assert evidence.event_id == "evt-1"
    assert evidence.source == "ActionEvent"


def test_probe_classifies_approval_grant_plumbing(probe):
    result = probe.probe_confirmation_approval_grant()
    assert result.name == "confirmation-approval-grant"
    evidence = result.evidence
    assert evidence["pending_event_fields"]
    assert "id" in evidence["pending_event_fields"]
    assert evidence["confirmation_endpoint"]
    assert evidence["normalized_argument_source"]
    assert evidence["grant_delivery"]
    assert evidence["openhands_holds_odysseus_signing_key"] is False
    assert result.passed is True
