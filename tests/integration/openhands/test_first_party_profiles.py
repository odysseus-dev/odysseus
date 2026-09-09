from __future__ import annotations

from pathlib import Path

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.first_party import FirstPartyProfileStack, OdysseusAgent  # noqa: E402

_PROFILES = Path(__file__).resolve().parents[3] / "deploy" / "openhands" / "profiles"


@pytest.fixture
def stack():
    return FirstPartyProfileStack(_PROFILES)


@pytest.mark.parametrize("profile", ["odysseus", "opencode", "hermes"])
def test_profile_revision_is_snapshotted(stack, profile):
    launched = stack.launch_profile(profile)
    assert launched.profile_id == profile
    assert launched.profile_revision
    assert launched.snapshot_rejects_unknown_fields


def test_odysseus_agent_is_composed_not_subclassed(stack):
    launched = stack.launch_profile("odysseus")
    agent = launched.agent
    assert isinstance(agent, OdysseusAgent)
    assert agent.base_agent == "CodeActAgent"
    assert "Odysseus" in agent.system_suffix
    assert "odysseus" in agent.mcp
    assert agent.archetype_context
    source = Path(OdysseusAgent.__module__.replace(".", "/") + ".py")
    # Composition marker: no business-rule imports on the agent object.
    assert agent.touches_database is False
    assert agent.touches_domain_services is False
    assert agent.touches_provider_clients is False
    assert not issubclass(OdysseusAgent, type(agent.base_agent))


def test_unknown_profile_field_is_rejected(stack, tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("agent_profile_id: x\nagent_profile_revision: 1\nmystery: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        FirstPartyProfileStack(tmp_path).launch_profile("x")
