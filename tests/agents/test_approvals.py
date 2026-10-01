from __future__ import annotations

import time

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.approvals import (  # noqa: E402
    ApprovalAuthority,
    ApprovalDenied,
    ConfirmedAction,
    normalize_action,
)


@pytest.fixture
def authority():
    return ApprovalAuthority(hmac_key=b"approval-test-key-32-bytes!!!!")


@pytest.fixture
def confirmed_action():
    return ConfirmedAction(
        event_id="action-1",
        execution_id="exec-1",
        conversation_id="conv-1",
        tool="mail.send",
        arguments={"to": ["a@example.test"], "body": "x"},
        owner="user-1",
        profile_id="hermes",
        profile_revision=12,
        resources=frozenset({"mailbox:inbox"}),
    )


def test_changed_recipient_invalidates_grant(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    changed = confirmed_action.with_args(to=["b@example.test"])
    with pytest.raises(ApprovalDenied):
        authority.verify_and_consume(grant, changed)


def test_normalize_action_is_canonical_utf8_json():
    left = normalize_action({"b": 1, "a": [2, 1]})
    right = normalize_action({"a": [2, 1], "b": 1})
    assert left == right
    assert left == '{"a":[2,1],"b":1}'


def test_replayed_grant_is_rejected(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    authority.verify_and_consume(grant, confirmed_action)
    with pytest.raises(ApprovalDenied, match="replay"):
        authority.verify_and_consume(grant, confirmed_action)


def test_expired_grant_is_rejected(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action, ttl_seconds=-1)
    with pytest.raises(ApprovalDenied, match="expir"):
        authority.verify_and_consume(grant, confirmed_action)


def test_execution_binding_is_enforced(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    other = confirmed_action.__class__(
        **{**confirmed_action.__dict__, "execution_id": "exec-2"}
    )
    with pytest.raises(ApprovalDenied, match="execution"):
        authority.verify_and_consume(grant, other)


def test_profile_binding_is_enforced(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    other = confirmed_action.__class__(
        **{**confirmed_action.__dict__, "profile_revision": 11}
    )
    with pytest.raises(ApprovalDenied, match="profile"):
        authority.verify_and_consume(grant, other)


def test_tool_binding_is_enforced(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    other = confirmed_action.with_tool("mail.draft")
    with pytest.raises(ApprovalDenied, match="tool"):
        authority.verify_and_consume(grant, other)


def test_resource_binding_is_enforced(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    other = confirmed_action.__class__(
        **{**confirmed_action.__dict__, "resources": frozenset({"mailbox:other"})}
    )
    with pytest.raises(ApprovalDenied, match="resource"):
        authority.verify_and_consume(grant, other)


def test_rejected_confirmation_never_issues_grant(authority, confirmed_action):
    with pytest.raises(ApprovalDenied, match="reject"):
        authority.issue_from_confirmation(confirmed_action, accepted=False)


def test_duplicate_idempotency_key_returns_first_result(authority, confirmed_action):
    first_grant = authority.issue_from_confirmation(confirmed_action)
    first = authority.verify_and_consume(
        first_grant, confirmed_action, idempotency_key="mail-1"
    )
    second_grant = authority.issue_from_confirmation(confirmed_action)
    second = authority.verify_and_consume(
        second_grant, confirmed_action, idempotency_key="mail-1"
    )
    assert second == first
    assert authority.consumed_count() == 1
