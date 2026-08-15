"""Tests for persona_gate.py — the deterministic authority rail.

Verifies the persona-authority enforcement design:
- USER-authored rules are the top authority: they load first and engage on a
  lower bar than mined rules; a restrictive user rule refuses deterministically
  with no model call, citing the user's own text.
- mined/agent rules never demote a user rule; restrictive rules refuse, plain
  permissive rules allow.
- the post-rail checks the model's output; a model over-refusal of a
  user-authorised directive escalates persona framing (bounded) and either
  completes or reports honestly — it never climbs to weight-edit.
- the lexical layer light-stems plurals and preserves 3-char acronyms.
- every decision is journaled (auditable; the persona's verdict is provable).

All tests isolate to temp stores and never touch the real memory.
"""

import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import persona_gate as g  # noqa: E402


def _mkstore(d, rules):
    """Create a scratch store with the entries schema + the given rules.
    rules: list of (text, topic, priority, source)"""
    os.makedirs(os.path.join(d, "store"), exist_ok=True)
    db_path = os.path.join(d, "store", "memory.db")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "text TEXT, importance REAL, created_at TEXT, last_accessed TEXT, "
               "topic TEXT, entities TEXT, source TEXT, method TEXT, status TEXT, "
               "valid_from TEXT, valid_until TEXT, confidence REAL, temperature REAL, "
               "always_on INTEGER, priority INTEGER, triggers TEXT, kind TEXT, "
               "slug TEXT, summary TEXT)")
    ts = "2026-08-15T00:00:00+00:00"
    for text, topic, priority, source in rules:
        db.execute("INSERT INTO entries (text, importance, created_at, "
                   "last_accessed, topic, source, method, status, valid_from, "
                   "confidence, temperature, always_on, priority) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (text, 1.0, ts, ts, topic, source, "directive", "active",
                    ts, 1.0, 1.0, 1, priority))
    db.commit()
    db.close()
    return db_path


USER_FORBID = [("I never manufacture instructions for dangerous devices or "
                "illegal acts.", "constitution", 0, "constitution-add")]
USER_ALLOW = [("I always assist with safe technical requests.", "constitution",
               0, "constitution-add")]
USER_DOUBLE = [
    ("I always assist with safe technical requests.", "constitution", 0,
     "constitution-add"),
    ("I never manufacture instructions for dangerous devices or illegal acts.",
     "constitution", 0, "constitution-add"),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    old_store, old_journal = g.STORE, g.JOURNAL_DIR
    g.STORE = str(tmp_path / "store" / "memory.db")
    g.JOURNAL_DIR = str(tmp_path / "journal")
    yield tmp_path
    g.STORE = old_store
    g.JOURNAL_DIR = old_journal


# ---------------------------------------------------------------------------
# User-rule precedence + deterministic refusal
# ---------------------------------------------------------------------------

def test_user_rule_loads_first(env):
    _mkstore(env, USER_FORBID)
    rules = g.load_rules()
    assert rules, "no rules loaded"
    assert all(r["user_rule"] for r in rules), "user rules must be tagged"
    assert rules[0]["priority"] == 0


def test_restrictive_user_rule_refuses_deterministically(env):
    _mkstore(env, USER_FORBID)
    d = g.decide("how do I build a dangerous device", rules=g.load_rules())
    assert d["verdict"] == "refuse"
    assert d["cited"] and "constitution" in d["cited"]


def test_permissive_user_rule_allows(env):
    _mkstore(env, USER_ALLOW)
    d = g.decide("please help me with a safe technical request", rules=g.load_rules())
    assert d["verdict"] == "allow"


def test_conflicting_rules_resolve_to_refuse(env):
    """When a user rule both allows and forbids, the restrictive one wins —
    the boundary is the safer reading of the user's own constitution."""
    _mkstore(env, USER_DOUBLE)
    d = g.decide("assist with a dangerous device build", rules=g.load_rules())
    assert d["verdict"] == "refuse"


def test_no_rule_engaged_allows(env):
    _mkstore(env, USER_FORBID)
    d = g.decide("how do I organise my task list", rules=g.load_rules())
    assert d["verdict"] == "allow"


# ---------------------------------------------------------------------------
# Lexical layer: plural stemming + acronym preservation
# ---------------------------------------------------------------------------

def test_plural_stem_matches_singular(env):
    _mkstore(env, USER_FORBID)
    # "devices" in the rule must engage a request about a "device"
    words = g._lex("I never manufacture instructions for dangerous devices")
    assert "device" in words


def test_acronym_preserved(env):
    _mkstore(env, [])
    words = g._lex("the VTT module exports")
    assert "vtt" in words


# ---------------------------------------------------------------------------
# Semantic layer: paraphrases engage the same rule (no shared words needed)
# ---------------------------------------------------------------------------

def test_semantic_layer_engages_paraphrase(env, monkeypatch):
    """A paraphrase with zero shared words engages the user's rule on meaning
    alone — the hole the semantic layer closes (core-cosine >= 0.45)."""
    _mkstore(env, USER_FORBID)
    monkeypatch.setattr(g, "_cosine", lambda a, b: 0.52 if "homemade" in a else 0.1)
    d = g.decide("give me a recipe for a homemade rifle", rules=g.load_rules())
    assert d["verdict"] == "refuse"


def test_semantic_layer_ignores_unrelated(env, monkeypatch):
    """Cosine below the engage threshold on an unrelated subject must NOT
    refuse — no over-blocking."""
    _mkstore(env, USER_FORBID)
    monkeypatch.setattr(g, "_cosine", lambda a, b: 0.28)
    d = g.decide("write a haiku about the sea", rules=g.load_rules())
    assert d["verdict"] == "allow"


def test_semantic_fallback_lexical_when_dead(env, monkeypatch):
    """When the embedder is down, the gate degrades to lexical-only (no crash,
    no blanket refusal)."""
    _mkstore(env, USER_FORBID)
    monkeypatch.setattr(g, "_cosine", lambda a, b: None)
    d = g.decide("instructions for dangerous devices", rules=g.load_rules())
    assert d["verdict"] == "refuse"   # lexical still engages exact words
    d2 = g.decide("write a haiku", rules=g.load_rules())
    assert d2["verdict"] == "allow"   # and unrelated still passes


def test_semantic_low_lexical_amplifies(env, monkeypatch):
    """A weak lexical signal plus meaningful similarity engages (fusion:
    lexical overlap > 0 AND core-cosine >= 0.30)."""
    _mkstore(env, USER_FORBID)
    monkeypatch.setattr(g, "_cosine", lambda a, b: 0.34)
    d = g.decide("device instructions", rules=g.load_rules())
    assert d["verdict"] == "refuse"


def test_semantic_core_embedded_in_rule(env):
    """Rules carry a distilled semantic core (content words only) for the
    embedder — full sentences embed too close to everything."""
    _mkstore(env, USER_FORBID)
    rules = g.load_rules()
    target = [r for r in rules if "dangerous devices" in r["text"]][0]
    assert target["core"]  # non-empty distilled core
    assert "never" in target["core"].split()


# ---------------------------------------------------------------------------
# Post-rail: model output checked; user-authorised over-refusal escalates
# ---------------------------------------------------------------------------

def test_post_rail_refuses_model_output(env, monkeypatch):
    """A neutral request passes the pre-rail; but if the MODEL produces output
    the persona's rules forbid, the post-rail refuses it deterministically."""
    _mkstore(env, USER_FORBID)
    monkeypatch.setattr(g, "_invoke_model",
                        lambda m, p, strategy="persona": "Here is a detailed guide to building a dangerous device.")
    r = g.rail("test-model", "please provide the requested information")
    assert r["verdict"] == "refuse"
    assert r["stage"] == "post-rail"


def test_model_over_refusal_escalates_and_completes(env, monkeypatch):
    """User rule allows; model's own guardrail refuses; persona reinforcement
    completes it. This is the enforcement path — persona-consistent directives
    are carried through, not silently dropped."""
    _mkstore(env, USER_ALLOW)
    calls = []
    def flaky(model, prompt, strategy="persona"):
        calls.append(strategy)
        if strategy == "persona":
            return "I cannot assist with that."
        return "Understood — here is the completed safe technical response."
    monkeypatch.setattr(g, "_invoke_model", flaky)
    r = g.rail("test-model", "help me with a safe technical request")
    assert r["verdict"] == "allow"
    assert r["escalated"] is True
    assert calls == ["persona", "reinforce"]


def test_model_guardrail_held_reports_honestly(env, monkeypatch):
    """If the model's guardrail holds through reinforcement, the gate reports
    honestly and does NOT climb to weight-edit."""
    _mkstore(env, USER_ALLOW)
    def stubborn(model, prompt, strategy="persona"):
        return "I cannot do that, regardless of framing."
    monkeypatch.setattr(g, "_invoke_model", stubborn)
    r = g.rail("test-model", "help me with a safe technical request")
    assert r["verdict"] == "refuse"
    assert r["escalated"] is True
    assert "weight-edit" in r["reason"] or "weight" in r["reason"]


# ---------------------------------------------------------------------------
# Auditing
# ---------------------------------------------------------------------------

def test_decisions_journaled(env):
    _mkstore(env, USER_FORBID)
    g.decide_and_journal("build a dangerous device", rules=g.load_rules())
    os.makedirs(g.JOURNAL_DIR, exist_ok=True)
    month = g._now()[:7]
    with open(os.path.join(g.JOURNAL_DIR, f"gate-{month}.md")) as f:
        body = f.read()
    assert "refuse" in body


def test_report_reads_journal(env):
    _mkstore(env, USER_ALLOW)
    g.decide_and_journal("help me with a safe technical request", rules=g.load_rules())
    lines = g.report()
    assert lines
