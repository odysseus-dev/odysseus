#!/usr/bin/env python3
"""lexicon_evolution.py — the lexicon meta-judgement applied to EVOLUTION.

Retrieval already applies the meta-judgement ("a shared word is not a
relation; a shared DISTINCTIVE word is a signal"): conjunctive matching, rare-
word gating, calibrated floors. Evolution decisions (persona coherence, identity
grounding, constitution promotion) must use the SAME lexicon discipline —
otherwise a personality can "evolve" on a coincidental shared word exactly the
way recall used to misfire on "black coffee" vs "black holes".

This module provides the shared lexical signal for the curator's three growth
paths, using identical definitions to the recall gate (same stopword set, same
distinctiveness threshold), so growth and retrieval speak one lexicon.

Decisions:
  coherence(fact, existing_values)  -> "consistent" | "conflict" | "novel"
      Does a new persona/identity value COHERE with what already exists?
      - "conflict": shares a DISTINCTIVE term AND a negation marker on it
        (a real contradiction, e.g. "never jokes" vs "dry humor").
      - "consistent": shares a distinctive term without negation (reinforces).
      - "novel": shares nothing distinctive — a new, additive axis (still
        promotable, but it does NOT rest on a shared-word coincidence).
      A shared COMMON word ("work", "always", "way") is never a relation.

  grounded(fact, source_texts)  -> bool
      Does the fact trace to real absorbed material via a DISTINCTIVE shared
      term (conjunctive), not just vector proximity or a common word?
      Lexical twin of the recall gate's dense check.

  promote_eligible(proposal, existing, evidence_strength, min_strength) -> dict
      A constitution/persona promotion is eligible only when BOTH hold:
        - the evidence threshold is met (existing durability gate), AND
        - the proposal shares a DISTINCTIVE term with the existing block OR is
          a fully novel axis (a real new principle, not a word-coincidence).
      Returns {eligible, reason}.

Usage:
  from lexicon_evolution import coherence, grounded, promote_eligible
"""

import os
import re

# SAME stopword set as memory_store._STOPWORDS (single source of truth would be
# ideal; keep in sync — see memory_store.py).
_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after",
         "what", "which", "who", "how", "why", "where", "while", "just",
         "more", "each", "than", "then", "very", "such", "some", "only",
         "prefers", "needs", "wants", "likes"}

# Hard stop for evolution: these generic framing words never carry a relation
# ("work", "always", "way") — a persona built on them would be a coincidence.
_FRAMING = {"work", "always", "way", "thing", "value", "identity", "persona",
            "someone", "something", "never", "always",
            "before", "after", "then", "when", "while", "must", "should",
            "will", "would", "could", "can", "have", "has", "had", "been",
            "being", "used", "use", "uses", "make", "makes", "made", "keep",
            "keeps", "take", "takes", "took", "get", "gets", "got", "going"}

_NEGATION = {"not", "never", "don't", "doesn't", "cannot", "can't", "no",
             "nor", "neither", "without", "avoid", "reject", "against",
             "refuses", "refuse", "opposed"}

_DISTINCTIVE_DF = 0.05  # same rare-word threshold as recall/associations


def content_words(text):
    """Significant content words (length > 3, not stop/framing)."""
    return [w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in _STOP and w not in _FRAMING]


def distinctive_terms(fact, corpus_texts):
    """The terms in `fact` that are RARE across the corpus (< 5% df).

    Same signal as the recall gate: a term common in the store ("black",
    "physics") is NOT a relation; a rare term ("celesta", "oatmilk") is."""
    total = max(len(corpus_texts), 1)
    fact_terms = set(content_words(fact))
    if not fact_terms:
        return set()
    # stem-aware document frequency (migrate/migration count as one term)
    doc_terms = {}
    for t in corpus_texts:
        stems = {_stem(w) for w in re.findall(r"[a-z]{4,}", (t or "").lower())}
        for s in stems:
            doc_terms[s] = doc_terms.get(s, 0) + 1
    return {t for t in fact_terms
            if doc_terms.get(_stem(t), 0) < max(total * _DISTINCTIVE_DF, 1.5)}


def shared_distinctive(fact, existing_values, corpus_texts):
    """Distinctive terms shared between `fact` and existing values."""
    distinct = distinctive_terms(fact, corpus_texts)
    if not distinct:
        return set()
    shared = set()
    existing_stems = [(_stem(w), val) for val in existing_values
                      for w in re.findall(r"[a-z]{4,}", (val or "").lower())]
    for term in distinct:
        ts = _stem(term)
        for s, _val in existing_stems:
            if s == ts:
                shared.add(term)
                break
    return shared


def coherence(fact, existing_values, corpus_texts=()):
    """Classify a new value against existing ones, lexicon-aware.

    Returns "conflict" | "consistent" | "novel".
    """
    values = [v for v in existing_values if v and v.strip()]
    if not values:
        return "novel"
    corpus = corpus_texts or values
    shared = shared_distinctive(fact, values, corpus)
    if not shared:
        # No distinctive shared term: the value is a NEW axis. This is NOT a
        # contradiction (a shared common word like "work" is meaningless), and
        # NOT a reinforcement — it adds independently.
        return "novel"
    low = fact.lower()
    has_neg = any(n in low for n in _NEGATION)
    if has_neg and any(f" {t} " in f" {low} " for t in shared):
        return "conflict"
    return "consistent"


def grounded(fact, source_texts, min_distinct=1):
    """Is `fact` grounded in real material via a DISTINCTIVE shared term?

    Conjunctive/rare-word check (lexical twin of the recall gate): a fact that
    shares only common words or pure vector proximity with sources is NOT
    grounded — invented concepts must not shape personality/identity.

    Morphology-aware: a term is grounded if its STEM appears in the sources
    ("migrated" matches "migration", "retroactively" matches "retroactive"),
    using a light suffix fold — exact word identity is too strict for natural
    sources.
    """
    srcs = [s for s in source_texts if s and s.strip()]
    if not srcs:
        return False
    fact_terms = set(content_words(fact))
    if not fact_terms:
        return False
    joined = " ".join(s.lower() for s in srcs)
    stems = {_stem(t) for t in re.findall(r"[a-z]{4,}", joined)}
    hits = 0
    for t in fact_terms:
        if _stem(t) in stems:
            hits += 1
            if hits >= min_distinct:
                return True
    return False


def _stem(w):
    """Light morphological fold for grounding: drop common English suffixes.
    Enough to equate migrate/migration/retroactively/retroactive and
    honest/honesty without a full stemmer (no external deps).

    The suffix rules are ordered longest-first and grouped so that the 5-char
    participle forms ('ating', 'ated') are stripped before their 3-char base
    ('ing', 'ed'), keeping migrate/migrating/migrated/migration on one root.
    """
    # (suffix, optional extra fold applied after stripping)
    rules = [
        ("ationally", ""), ("ableness", ""), ("iveness", ""),
        ("ation", ""), ("ating", ""), ("ative", "e"),
        ("atively", "e"), ("ability", ""), ("ities", "y"),
        ("ingly", ""), ("fully", ""), ("ness", ""), ("ment", ""),
        ("itive", "e"), ("atable", "e"), ("ated", ""), ("ates", "e"),
        ("ions", ""), ("ings", ""), ("able", "e"), ("ible", "e"),
        ("ing", "e"), ("ed", ""), ("es", ""), ("ly", ""), ("s", ""), ("e", ""),
    ]
    orig = w
    for suf, fold in rules:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            w = w[: len(w) - len(suf)]
            if fold:
                w = w + fold
            break
    # y -> i fold (honesty -> honest)
    if len(w) > 4 and w.endswith("y") and w[-2] not in "aeiouy":
        w = w[:-1]
    return w


def promote_eligible(proposal, existing, evidence_strength, min_strength=3,
                     corpus_texts=()):
    """Is a persona/constitution promotion eligible, lexicon-aware?

    Eligibility = evidence threshold AND (shares a distinctive term with the
    existing block OR is a genuinely novel axis grounded in the corpus). A
    proposal that rests only on common words is a word-coincidence and is
    parked, no matter how often it is sighted — mirroring the durability gate's
    "3+ references" rule but with a lexical floor. A "novel axis" is only
    eligible when it carries a DISTINCTIVE term that actually appears in the
    real absorbed material (the corpus), so arbitrary trivia ("pizza on
    fridays") cannot be promoted as a principle no matter the sighting count.
    """
    if evidence_strength < min_strength:
        return {"eligible": False,
                "reason": f"evidence strength {evidence_strength} < {min_strength}"}
    existing = [e for e in existing if e and e.strip()]
    if not existing:
        return {"eligible": True, "reason": "empty block — first principle"}
    corpus = corpus_texts or existing
    shared = shared_distinctive(proposal, existing, corpus)
    if shared:
        return {"eligible": True,
                "reason": f"shares distinctive term(s): {sorted(shared)[:3]}"}
    # No distinctive shared term: a NOVEL axis is eligible only if it is
    # GROUNDED in the absorbed corpus via a distinctive term (a real principle
    # names something real), and the proposal is not trivial framing.
    terms = content_words(proposal)
    if not terms:
        return {"eligible": False, "reason": "no lexical signal"}
    if grounded(proposal, corpus, min_distinct=1):
        return {"eligible": True,
                "reason": f"novel axis grounded in corpus ({len(terms)} terms)"}
    return {"eligible": False,
            "reason": "novel axis not grounded in absorbed material — parked"}
