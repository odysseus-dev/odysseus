"""Tests for taxonomy.py — automatic category + subcategory assignment.

Verifies the automation design: a claim is sorted into a wing and subcategory
WITHOUT the user naming either. Uses the local embedder (mxbai) so a politics
claim lands in politics, a memory-system claim in opencode_memory, etc.
Unmatched claims fall to 'general' — never silently dropped.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import taxonomy  # noqa: E402


@pytest.fixture(autouse=True)
def iso(tmp_path):
    old = taxonomy.TAXONOMY_FILE
    taxonomy.TAXONOMY_FILE = str(tmp_path / "taxonomy.json")
    yield tmp_path
    taxonomy.TAXONOMY_FILE = old


def test_politics_claim_classifies(iso):
    res = taxonomy.classify("we live in a capitalist society")
    assert res["wing"] == "politics"
    assert res["subcategory"] == "economic systems"
    assert res["confidence"] == "high"


def test_memory_system_claim_classifies(iso):
    res = taxonomy.classify("the memory system uses warm neurons and a store")
    assert res["wing"] == "opencode_memory"
    assert res["subcategory"] == "warm neurons"


def test_ttrpg_claim_classifies(iso):
    res = taxonomy.classify("impossible landscapes is a delta green campaign")
    assert res["wing"] == "deltagreen"


def test_guitar_marketing_claim_classifies(iso):
    res = taxonomy.classify("the guitar academy onboarding funnel needs work")
    assert res["wing"] == "guitar-marketing"


def test_human_claim_classifies(iso):
    res = taxonomy.classify("nick prefers oat milk and short replies")
    assert res["wing"] == "human"
    assert res["subcategory"] == "preferences"


def test_unmatched_falls_to_general(iso):
    res = taxonomy.classify("the mitochondria is the powerhouse of the cell")
    assert res["wing"] == "general"
    assert res["confidence"] == "low"


def test_wings_lists_taxonomy(iso):
    ws = taxonomy.wings()
    names = {w["wing"] for w in ws}
    assert "politics" in names and "human" in names
    assert any(w["subcategories"] for w in ws if w["wing"] == "politics")


def test_add_wing(iso):
    res = taxonomy.add_wing("astronomy", "stars, planets, galaxies, space")
    assert res["status"] == "added"
    assert any(w["wing"] == "astronomy" for w in taxonomy.wings())


def test_add_wing_existing(iso):
    taxonomy.add_wing("astronomy", "stars, planets, galaxies")
    res = taxonomy.add_wing("astronomy", "something else")
    assert res["status"] == "exists"
