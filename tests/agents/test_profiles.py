from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.archetypes import load_archetypes  # noqa: E402
from services.agents.profiles import (  # noqa: E402
    load_profile_policies,
    select_compatible_profiles,
)

_ROOT = Path(__file__).resolve().parents[2]
_POLICY = _ROOT / "config" / "agents" / "profile-policy.yaml"
_ARCHETYPES = _ROOT / "config" / "agents" / "archetypes"


def test_shipped_profile_policy_requires_revision():
    policies = load_profile_policies(_POLICY)
    assert policies
    for policy in policies:
        assert policy.agent_profile_revision is not None
        assert policy.policy_revision is not None


def test_select_compatible_profiles_for_chat():
    archetypes = load_archetypes(_ARCHETYPES)
    chat = next(item for item in archetypes if item.id == "chat")
    policies = load_profile_policies(_POLICY)
    selected = select_compatible_profiles(chat, policies)
    assert {item.agent_profile_id for item in selected} >= {"odysseus", "hermes"}


def test_incompatible_profile_is_excluded():
    archetypes = load_archetypes(_ARCHETYPES)
    research = next(item for item in archetypes if item.id == "deep-research")
    policies = load_profile_policies(_POLICY)
    selected = select_compatible_profiles(research, policies)
    ids = {item.agent_profile_id for item in selected}
    assert "hermes" in ids
    assert "opencode" not in ids


def test_load_rejects_unknown_profile_keys(tmp_path):
    path = tmp_path / "profile-policy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "profiles": [
                    {
                        "agent_profile_id": "hermes",
                        "agent_profile_revision": 12,
                        "capabilities": ["research.invoke"],
                        "archetypes": ["chat"],
                        "risk_class": "medium",
                        "delegation_depth": 1,
                        "workspace_classes": ["research"],
                        "budget_class": "research-standard",
                        "distribution_metadata": {},
                        "policy_revision": 7,
                        "extra": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown"):
        load_profile_policies(path)


def test_select_rejects_capability_mismatch():
    archetypes = load_archetypes(_ARCHETYPES)
    research = next(item for item in archetypes if item.id == "deep-research")
    policies = [
        policy
        for policy in load_profile_policies(_POLICY)
        if policy.agent_profile_id == "odysseus"
    ]
    assert select_compatible_profiles(research, policies) == []
