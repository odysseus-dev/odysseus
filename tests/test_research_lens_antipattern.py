"""Tests for research_lens.py — the baloney-detector anti-pattern correction.

The 2026-08-15 correction: context-dependence is NOT a rejection signal. A
falsifiable, quantitative finding embedded in structural/institutional
conditions is where the mechanism lives — it must be flagged for
investigation, never used to discount the finding. Also: numeric evidence is
quantitative even without the literal token 'percent'.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import research_lens as rl  # noqa: E402


def test_quantitative_finding_with_context_is_strong():
    """The deaths-of-despair case: numbers + comparison + structural context
    must be STRONG with the mechanism flagged — NOT rejected for having a
    surrounding mechanism."""
    r = rl.assess_source(
        "Deaths of despair and the future of capitalism",
        "158000 deaths of despair in 2018 vs 65000 in 1995, attributed to "
        "institutional and structural factors; inequality gradient explains context")
    assert r["verdict"] == "STRONG"
    assert "institutional" in r["mechanism"]
    assert "inequality" in r["mechanism"]


def test_numeric_evidence_counts_as_quantitative():
    """Numbers with comparison language are quantitative evidence even without
    the token 'percent' — the bug that scored data-rich claims at zero."""
    assert rl._quantitative_signal("158000 deaths in 2018 vs 65000 in 1995") is True
    assert rl._quantitative_signal("the top 10 percent own 77 percent") is True
    assert rl._quantitative_signal("no numbers here, just a claim") is False


def test_context_dependence_never_drags_strong_down():
    """A quantitative finding mentioning context must not be rejected for it."""
    r = rl.assess_source(
        "Wealth concentration study",
        "77 percent of wealth held by the top decile, due to structural and "
        "institutional factors")
    assert r["verdict"] in ("STRONG", "WEAK")
    assert r["verdict"] != "REJECT"


def test_overclaim_still_rejects():
    """The correction must NOT let genuinely weak claims through — overclaim
    and sensationalism still disqualify regardless of context language."""
    r = rl.assess_source(
        "Definitely proven miracle",
        "guaranteed shocking secret, experts say it is 100 percent certain, "
        "no doubt, absolutely")
    assert r["verdict"] == "REJECT"


def test_plain_weak_claim_is_not_promoted():
    """A bare claim with no numbers, no falsifiability, no mechanism is not
    promoted by the correction (it stays REJECT for lack of any evidence)."""
    r = rl.assess_source("Something is happening", "it is probably bad")
    assert r["verdict"] in ("WEAK", "REJECT")
    assert r["verdict"] != "STRONG"


def test_weak_claim_gets_constructive_guidance():
    """The kit is a lamp, not a weapon: a weak claim is told what would
    strengthen it, not just condemned. This is the wonder-skepticism balance."""
    r = rl.assess_source("Something is bad", "experts claim it is probably harmful")
    assert r["next_steps"], "weak claim must receive constructive next steps"
    joined = " ".join(r["next_steps"]).lower()
    assert "strengthen" in joined
    assert ("data" in joined) or ("falsif" in joined)


def test_strong_finding_suggests_deepening_not_rejection():
    """Even a strong finding gets 'deepen it further' guidance with mechanism
    investigation — never antagonism."""
    r = rl.assess_source(
        "Wealth concentration",
        "77 percent of wealth held by the top decile due to structural inequality")
    assert r["verdict"] == "STRONG"
    assert "deepen" in " ".join(r["next_steps"]).lower()
    assert r["mechanism"]  # mechanism flagged for investigation


def test_reject_still_gets_corrective_guidance():
    """A REJECT is corrected with a path forward, not just condemned."""
    r = rl.assess_source(
        "Definitely proven miracle",
        "guaranteed 100 percent certain, absolutely no doubt, shocking")
    assert r["verdict"] == "REJECT"
    assert r["next_steps"]
    assert "strengthen" in " ".join(r["next_steps"]).lower()
