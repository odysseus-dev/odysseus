from __future__ import annotations

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.delegation import DelegationAuthority  # noqa: E402
from services.agents.first_party import FirstPartyProfileStack  # noqa: E402


def test_local_subagent_shares_parent_execution_and_narrows_scope():
    stack = FirstPartyProfileStack()
    parent = stack.launch_execution(profile="odysseus", execution_id="R100")
    child = stack.launch_local_subagent(parent, scopes={"notes.read"}, delegation_id="del-1")
    assert child.execution_id == parent.execution_id
    assert child.delegation_id == "del-1"
    assert child.scopes == frozenset({"notes.read"})
    assert child.scopes < parent.scopes


def test_managed_hermes_child_gets_fresh_execution_and_subset_token():
    authority = DelegationAuthority(hmac_key=b"profile-delegation-key-32-bytes")
    stack = FirstPartyProfileStack(authority=authority)
    parent = stack.launch_execution(profile="odysseus", execution_id="R100", authority=authority)
    child = stack.launch_managed_child(parent, profile="hermes", archetype="deep-research")
    assert child.execution_id != parent.execution_id
    assert child.profile_id == "hermes"
    assert child.profile_revision
    assert child.budget <= parent.budget
    assert child.workspace_policy
    assert child.token.claims.scopes <= parent.token.claims.scopes
