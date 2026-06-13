# tests/test_platform_profiles.py
"""Seed-skill installer, métier catalog loader, profile compiler. Spec §4, §5."""
import json
from pathlib import Path

import pytest
import yaml

from services.business_platform.profile_compiler import (
    CatalogError, GENERAL_OFFICE_CATALOG_PATH, SEED_SKILLS_DIR,
    compile_profile, install_seed_skills, load_catalog,
)

# ------------------------------------------------------------- installer ----


def test_install_seed_skills_missing_only(tmp_path):
    r1 = install_seed_skills(tmp_path)
    assert len(r1["installed"]) == 6 and not r1["skipped"]
    assert (tmp_path / "marketing" / "seo" / "SKILL.md").exists()
    # operator edit survives a re-install
    edited = tmp_path / "marketing" / "seo" / "SKILL.md"
    edited.write_text("OPERATOR EDIT")
    r2 = install_seed_skills(tmp_path)
    assert not r2["installed"] and len(r2["skipped"]) == 6
    assert edited.read_text() == "OPERATOR EDIT"


def test_seeds_load_via_skills_manager(tmp_path):
    from services.memory.skills import SkillsManager
    install_seed_skills(tmp_path / "skills")
    skills = SkillsManager(str(tmp_path)).load_all()
    assert {s["name"] for s in skills} == {
        "seo", "content-writer", "page-writer", "web-search",
        "email-triage", "task-queue",
    }


# ---------------------------------------------------------------- loader ----


def test_shipped_general_office_catalog_loads():
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    assert cat["vertical"] == "general_office"
    assert set(cat["roles"]) == {
        "seo", "content", "front-desk", "support", "sales", "office-manager",
    }
    assert set(cat["gated_classes"]) == {
        "payment_refund", "outbound_comms", "quote",
    }
    assert cat["front_desk"]["*"] in cat["roles"]
    for role, spec in cat["roles"].items():
        assert spec["soul"].strip(), role
        assert isinstance(spec["tools"], list) and spec["tools"], role


def _write(tmp_path, data):
    p = tmp_path / "cat.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def _valid_minimal():
    return {
        "vertical": "shoptest",
        "display_name": "Shop Test",
        "surface_policy": "web_first",
        "gated_classes": ["quote"],
        "front_desk": {"quote.": "clerk", "*": "clerk"},
        "roles": {
            "clerk": {"description": "d", "soul": "You are a clerk.",
                      "tools": ["memory"], "skills": []},
        },
    }


def test_minimal_catalog_valid(tmp_path):
    cat = load_catalog(_write(tmp_path, _valid_minimal()), skills_dir=tmp_path)
    assert cat["vertical"] == "shoptest"


def test_unknown_gated_class_rejected(tmp_path):
    bad = _valid_minimal()
    bad["gated_classes"] = ["quote", "world_domination"]
    with pytest.raises(CatalogError, match="gated"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_route_to_missing_role_rejected(tmp_path):
    bad = _valid_minimal()
    bad["front_desk"]["payment."] = "ghost-role"
    with pytest.raises(CatalogError, match="ghost-role"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_missing_catchall_rejected(tmp_path):
    bad = _valid_minimal()
    del bad["front_desk"]["*"]
    with pytest.raises(CatalogError, match="catch-all"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_empty_roles_rejected(tmp_path):
    bad = _valid_minimal()
    bad["roles"] = {}
    with pytest.raises(CatalogError, match="roles"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_bad_surface_policy_rejected(tmp_path):
    bad = _valid_minimal()
    bad["surface_policy"] = "carrier_pigeon"
    with pytest.raises(CatalogError, match="surface_policy"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_unresolvable_skill_ref_rejected(tmp_path):
    bad = _valid_minimal()
    bad["roles"]["clerk"]["skills"] = ["nowhere/ghost-skill"]
    with pytest.raises(CatalogError, match="ghost-skill"):
        load_catalog(_write(tmp_path, bad), skills_dir=tmp_path)


def test_seed_skill_ref_resolves_without_native_install(tmp_path):
    ok = _valid_minimal()
    ok["roles"]["clerk"]["skills"] = ["marketing/seo"]   # seed, not in tmp_path
    cat = load_catalog(_write(tmp_path, ok), skills_dir=tmp_path)
    assert cat["roles"]["clerk"]["skills"] == ["marketing/seo"]


# -------------------------------------------------------------- compiler ----


def test_compile_general_office(tmp_path):
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    manifest = compile_profile(cat, tmp_path)
    assert len(manifest["personas"]) == 6 and len(manifest["agents"]) == 6

    # SEO role end-to-end (golden expectations, spec §5)
    soul = (tmp_path / "personas" / "general_office-seo" / "SOUL.md").read_text()
    assert soul == cat["roles"]["seo"]["soul"].rstrip() + "\n"
    meta = json.loads(
        (tmp_path / "personas" / "general_office-seo" / "meta.json").read_text())
    assert meta == {"description": cat["roles"]["seo"]["description"]}
    agent = json.loads(
        (tmp_path / "agents" / "general_office-seo" / "agent.json").read_text())
    assert agent == {
        "persona": "general_office-seo",
        "tools": ["web", "documents", "memory"],
        "skills": ["marketing/seo", "research/web-search"],
        "model": None,
    }

    front = json.loads((tmp_path / "front_desk.json").read_text())
    assert front["front_desk"]["*"] == "front-desk"
    assert front["gated_classes"] == ["outbound_comms", "payment_refund", "quote"]
    assert front["surface_policy"] == "web_first"


def test_compile_idempotent(tmp_path):
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    compile_profile(cat, tmp_path)
    first = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    compile_profile(cat, tmp_path)
    second = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert first == second
