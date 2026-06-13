# tests/test_agent_profiles.py
"""Persona/agent profile loading + owner derivation (multiagent slice-1).

Formats are the ones the business-platform profile compiler emits:
personas/<name>/SOUL.md (+ meta.json), agents/<name>/agent.json.
"""
import json

import pytest

from services.agents.profile import (
    ProfileError, derive_owner, load_agent, load_persona, resolve_binding,
)


@pytest.fixture()
def profiles_root(tmp_path):
    """A data root with one compiled persona+agent (compiler round-trip)."""
    from services.business_platform.profile_compiler import (
        GENERAL_OFFICE_CATALOG_PATH, SEED_SKILLS_DIR, compile_profile,
        load_catalog,
    )
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    compile_profile(cat, tmp_path)
    return tmp_path


def test_load_persona_roundtrip(profiles_root):
    p = load_persona("general_office-seo", data_dir=profiles_root)
    assert "SEO specialist" in p["soul"]
    assert p["description"]


def test_load_agent_roundtrip(profiles_root):
    a = load_agent("general_office-seo", data_dir=profiles_root)
    assert a["persona"] == "general_office-seo"
    assert a["tools"] == ["web", "documents", "memory"]
    assert a["skills"] == ["marketing/seo", "research/web-search"]
    assert a["model"] is None


def test_missing_persona_and_agent_raise(profiles_root):
    with pytest.raises(ProfileError, match="ghost"):
        load_persona("ghost", data_dir=profiles_root)
    with pytest.raises(ProfileError, match="ghost"):
        load_agent("ghost", data_dir=profiles_root)


def test_derive_owner():
    assert derive_owner("oleg", "researcher") == "agent:oleg/researcher"
    assert derive_owner(None, "researcher") == "agent:local/researcher"
    assert derive_owner("", "researcher") == "agent:local/researcher"
    # agent ids must never nest/imitate (no second derivation from an agent id)
    with pytest.raises(ProfileError, match="agent"):
        derive_owner("agent:oleg/researcher", "sub")


def test_resolve_binding_stored_agent(profiles_root):
    b = resolve_binding({"agent": "general_office-seo", "task": "audit site"},
                        human="oleg", data_dir=profiles_root)
    assert b["owner"] == "agent:oleg/general_office-seo"
    assert b["tools"] == ["web", "documents", "memory"]
    assert "SEO specialist" in b["soul"]
    assert b["task"] == "audit site"
    assert b["model"] is None


def test_resolve_binding_inline(profiles_root):
    b = resolve_binding(
        {"persona": "general_office-content", "tools": ["documents"],
         "task": "summarize doc 41"},
        human="oleg", data_dir=profiles_root)
    assert b["owner"] == "agent:oleg/general_office-content"
    assert b["tools"] == ["documents"]
    assert "content writer" in b["soul"]


def test_resolve_binding_requires_agent_or_persona(profiles_root):
    with pytest.raises(ProfileError, match="agent.*persona|persona.*agent"):
        resolve_binding({"task": "do something"}, human="oleg",
                        data_dir=profiles_root)
    with pytest.raises(ProfileError, match="task"):
        resolve_binding({"agent": "general_office-seo"}, human="oleg",
                        data_dir=profiles_root)
