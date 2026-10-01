from __future__ import annotations

import logging
import time

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.delegation import (  # noqa: E402
    DelegationAuthority,
    DelegationDenied,
    WorkloadAuthenticationRequired,
)


class MemoryWorkload:
    def __init__(self, identity: str = "workload-1") -> None:
        self.identity = identity

    def authenticate(self) -> str:
        return self.identity


@pytest.fixture
def authority():
    return DelegationAuthority(hmac_key=b"delegation-test-key-32-bytes!!")


def _issue(authority, **overrides):
    payload = {
        "audience": "odysseus-mcp",
        "execution_id": "exec-1",
        "conversation_id": "conv-1",
        "owner": "user-1",
        "scopes": {"notes.read", "mail.send"},
        "resources": {"note:1", "mailbox:inbox"},
        "profile_id": "hermes",
        "profile_revision": 12,
        "archetype_id": "deep-research",
        "archetype_version": 1,
        "budget": 100,
        "max_depth": 1,
        "ttl_seconds": 60,
        "allowed_child_archetypes": {"deep-research", "chat"},
    }
    payload.update(overrides)
    return authority.issue(**payload)


def test_child_cannot_expand_parent(authority):
    parent = _issue(authority)
    with pytest.raises(DelegationDenied):
        authority.issue_child(parent, scopes={"notes.read", "mail.send", "calendar.write"})


def test_agent_cannot_reissue(authority):
    token = _issue(authority)
    with pytest.raises(WorkloadAuthenticationRequired):
        authority.reissue(token, workload=None)


def test_wrong_audience_is_denied(authority):
    token = _issue(authority, audience="odysseus-mcp")
    with pytest.raises(DelegationDenied, match="audience"):
        authority.verify(token, audience="other", execution_id="exec-1")


def test_expired_token_is_denied(authority):
    token = _issue(authority, ttl_seconds=-1)
    with pytest.raises(DelegationDenied, match="expir"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-1")


def test_cancelled_execution_is_denied(authority):
    token = _issue(authority)
    authority.cancel_execution("exec-1")
    with pytest.raises(DelegationDenied, match="cancel"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-1")


def test_cancelled_token_id_is_denied(authority):
    token = _issue(authority)
    authority.revoke(token.claims.token_id)
    with pytest.raises(DelegationDenied, match="revok"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-1")


def test_child_cannot_add_resources(authority):
    parent = _issue(authority)
    with pytest.raises(DelegationDenied):
        authority.issue_child(parent, scopes={"notes.read"}, resources={"note:1", "note:2"})


def test_child_cannot_increase_budget(authority):
    parent = _issue(authority, budget=10)
    with pytest.raises(DelegationDenied):
        authority.issue_child(parent, scopes={"notes.read"}, budget=11)


def test_child_depth_cannot_exceed_parent(authority):
    parent = _issue(authority, max_depth=0)
    with pytest.raises(DelegationDenied, match="depth"):
        authority.issue_child(parent, scopes={"notes.read"})


def test_child_archetype_must_be_allowed(authority):
    parent = _issue(authority, allowed_child_archetypes={"chat"})
    with pytest.raises(DelegationDenied, match="archetype"):
        authority.issue_child(
            parent,
            scopes={"notes.read"},
            archetype_id="deep-research",
            profile_id="hermes",
            profile_revision=12,
        )


def test_profile_revision_is_bound(authority):
    token = _issue(authority, profile_revision=12)
    claims = authority.verify(token, audience="odysseus-mcp", execution_id="exec-1")
    assert claims.profile_revision == 12
    with pytest.raises(DelegationDenied):
        authority.verify(
            token,
            audience="odysseus-mcp",
            execution_id="exec-1",
            profile_revision=11,
        )


def test_execution_id_is_bound(authority):
    token = _issue(authority, execution_id="exec-1")
    with pytest.raises(DelegationDenied, match="execution"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-2")


def test_reissue_requires_workload_and_rotates_token_id(authority):
    token = _issue(authority)
    refreshed = authority.reissue(token, workload=MemoryWorkload())
    assert refreshed.claims.token_id != token.claims.token_id
    authority.verify(refreshed, audience="odysseus-mcp", execution_id="exec-1")
    with pytest.raises(DelegationDenied, match="revok"):
        authority.verify(token, audience="odysseus-mcp", execution_id="exec-1")


def test_valid_child_is_narrower(authority):
    parent = _issue(authority, max_depth=1)
    child = authority.issue_child(parent, scopes={"notes.read"}, resources={"note:1"})
    claims = authority.verify(child, audience="odysseus-mcp", execution_id="exec-1")
    assert claims.scopes == frozenset({"notes.read"})
    assert claims.resources == frozenset({"note:1"})
    assert claims.depth == 1
    assert claims.parent_token_id == parent.claims.token_id


def test_tampered_signature_is_denied(authority):
    token = _issue(authority)
    tampered = token.__class__(claims=token.claims, signature="00" * 32)
    with pytest.raises(DelegationDenied, match="signature"):
        authority.verify(tampered, audience="odysseus-mcp", execution_id="exec-1")


def test_errors_are_log_safe(authority, caplog):
    caplog.set_level(logging.INFO)
    token = _issue(authority)
    with pytest.raises(DelegationDenied) as excinfo:
        authority.verify(token, audience="other", execution_id="exec-1")
    text = str(excinfo.value)
    assert token.signature not in text
    assert token.claims.token_id not in caplog.text or token.signature not in caplog.text
    assert token.signature not in caplog.text
