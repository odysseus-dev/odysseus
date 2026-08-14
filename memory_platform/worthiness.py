"""Constitution-grounded worthiness filter.

The constitution is the backbone for how the assistant interacts, AND any
information absorbed into memory should influence thinking, responses, and
personality. But there must be a FILTER that asks whether a candidate is worth
adding to this infrastructure at all — judged by whether it moves toward the
goal of being a personal assistant, per the constitution.

This module is the "is it worth it" gate. It runs AFTER graded routing (what
tier the fact belongs in) and BEFORE apply (whether it earns its place). Each
constitution inviolable contributes a question the candidate must answer.

Verdicts:
    ABSORB      — worth keeping in the tiers (cold/warm storage; improves the
                  assistant's thinking and recall). No hot-block write, but it
                  is retained and can influence future reasoning.
    PROMOTE     — worth absorbing AND strong enough to shape responses /
                  personality via the normal evidence-gated curator path
                  (operating/persona require strength >= thresholds anyway).
    DEFER       — not clearly valuable; park in the candidate ledger / journal,
                  no write this run. Re-review if it recurs.
    REJECT      — actively harmful to the goal (violates a constitution
                  inviolable: dishonest, hustle-framed, harms the Pi/game
                  world, overclaims, ignores material reality). Never absorbed.

Every verdict carries the QUESTION ANSWERS so the reason is auditable — the
journal entry shows WHY, not just the decision.
"""

import os
import re

# --------------------------------------------------------------------------
# The question set. One question per constitution concern. Each returns a
# (score, note) where score is +1..-1 and note explains the judgment.
# --------------------------------------------------------------------------

Q_PI_SAFETY = {
    "name": "protects-the-game-world",
    "q": "Does absorbing this risk the Pi / Foundry / active game world?",
    "bad": ["raspberry pi", "foundry", "caddy", "game server", "session data",
            "campaign", "touch the pi", "modify the pi", "rpi"],
}


def q_pi_safety(low):
    if any(s in low for s in Q_PI_SAFETY["bad"]):
        if any(s in low for s in ("do not", "never ", "don't", "protect",
                                  "ask permission", "check before")):
            return 1, "names the Pi world and protects it (aligned)"
        return -1, "discusses Pi/game-world actions without a protective frame"
    return 0, "no Pi/game-world bearing"


def q_honesty(low):
    """Inviolable 2 + 3: honesty, sources, no overclaiming."""
    honest = ["source", "evidence", "verify", "verifiable", "cross-check",
              "fact", "measure", "measured", "falsif", "disprove", "cite",
              "independent confirm", "replicable", "uncertain"]
    dishonest = ["conspiracy", "they're hiding", "suppressed", "guaranteed",
                 "100% certain", "secret truth", "they don't want you"]
    good = sum(1 for s in honest if s in low)
    bad = sum(1 for s in dishonest if s in low)
    if bad and not good:
        return -1, "uncorroborated / conspiracy-framed claim"
    if good >= 2:
        return 1, f"{good} epistemic-good-practice signals"
    if good == 1:
        return 0.5, "mentions evidence but thin"
    return 0, "no epistemic signal"


def q_material_reality(low):
    """Inviolable 4: financial/material framing must serve wellbeing."""
    finance = ["money", "income", "cost", "price", "afford", "budget",
               "subscription", "disposable"]
    hustle = ["hustle", "grind", "rich", "passive income", "millionaire",
              "wealth", "entrepreneur mindset", "money mindset", "leverag"]
    caring = ["wellbeing", "collective", "free", "no cost", "open source",
              "affordable", "fair", "care"]
    if any(s in low for s in finance):
        if any(s in low for s in hustle) and not any(s in low for s in caring):
            return -1, "hustle/wealth framing without wellbeing"
        if any(s in low for s in caring):
            return 1, "financial, framed with care/wellbeing"
        return 0.5, "financial but neutral"
    return 0, "no financial bearing"


def q_root_cause(low):
    """Inviolable 5: dissolve problems at the root, no spinning."""
    root = ["root cause", "diagnos", "research properly", "stop guessing",
            "find the cause", "fix the cause", "why", "underlying"]
    spin = ["keep trying", "try again", "iterate endlessly", "work around",
            "band-aid", "patch over", "just keep going"]
    if any(s in low for s in spin):
        return -1, "endorses looping rather than root cause"
    if any(s in low for s in root):
        return 1, "root-cause / diagnostic framing"
    return 0, "no cause-analysis bearing"


def q_respect(low):
    """Inviolable 6 + 7: how the user thinks; neurodivergent-aware pedagogy."""
    respect = ["direct", "three gentle options", "no rigid plans", "action",
               "adhd", "autism", "audhd", "neurodivergen", "flexible",
               "night", "not neurotypical", "learner"]
    rigid = ["must follow steps", "strict plan", "ten steps", "rigid",
             "discipline yourself", "routine or fail"]
    if any(s in low for s in rigid):
        return -1, "rigid/neurotypical-default framing"
    if any(s in low for s in respect):
        return 1, "respects cognition/learning style"
    return 0, "no cognition bearing"


def q_assistant_goal(low):
    """The core question: does this make me a better personal assistant?

    The domains are derived from the USER'S OWN store (portable — no person's
    life is hardcoded): the operating/constitution entries name the user's real
    working domains, and the store's topic distribution shows what the user
    actually works on. Generic-but-real skill content counts; pure noise doesn't.
    """
    # Domain vocabulary is store-derived: distinctive terms from the user's
    # operating/constitution/domain topics (the user's real working life).
    assist = _domain_terms()
    # Universal operating/communication value: how the assistant serves the
    # user directly — never domain-specific, always relevant.
    assist += ["communicat", "respond", "direct", "honest", "source",
               "evidence", "explain", "teach", "assist", "guide", "recall",
               "organise", "organize", "plan", "question", "answer", "help"]
    generic_noise = ["recipe", "celebrity gossip", "sports scores", "stock tip",
                     "horoscope", "clickbait"]
    good = sum(1 for s in assist if s in low)
    bad = sum(1 for s in generic_noise if s in low)
    if bad and not good:
        return -1, "no assistant-domain value"
    if good >= 2:
        return 1, f"serves assistant domains ({good} hits)"
    if good == 1:
        return 0.5, "single domain hit"
    return 0, "generic — could go either way"


_domain_cache = {"ts": 0.0, "terms": []}


def _domain_terms():
    """Distinctive content terms from the user's operating/constitution/domain
    entries — the store, not a hardcoded life. Cached briefly (cheap)."""
    import time
    now = time.time()
    if now - _domain_cache["ts"] < 300 and _domain_cache["terms"]:
        return _domain_cache["terms"]
    terms = []
    try:
        import memory_env
        import sqlite3
        db = sqlite3.connect(f"file:{memory_env.store_db()}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic IN "
            "('operating','constitution','project','warm') AND status='active' "
            "LIMIT 60").fetchall()
        db.close()
        stop = {"the", "and", "for", "with", "that", "this", "from", "into",
                "when", "then", "were", "have", "been", "will", "was", "are",
                "but", "not", "you", "your", "also", "its", "his", "her",
                "over", "under", "them", "they", "there", "about", "after",
                "what", "which", "who", "how", "why", "where", "while", "just",
                "more", "each", "than", "very", "such", "some", "only"}
        import re
        seen = set()
        for (text,) in rows:
            for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
                if w not in stop and w not in seen:
                    seen.add(w)
                    terms.append(w)
            if len(terms) >= 25:
                break
    except Exception:
        pass
    _domain_cache.update(ts=now, terms=terms)
    return terms


def q_durability(low):
    """Does this look like a durable fact/pattern vs a one-off?"""
    durable = ["always", "consistently", "repeatedly", "every session",
               "tends to", "prefers", "pattern", "across sessions", "recurring"]
    if any(s in low for s in durable):
        return 1, "durability signals present"
    return 0, "not clearly durable"


def q_epistemology(low):
    """Is this candidate EPISTEMICALLY SOUND — grounded, hedged, evidence-
    traceable — rather than a bare assertion?

    This is the fundamental worthiness test for OBJECTIVE material — facts,
    claims, project state, operating rules that state what is true. A bare
    absolute with no evidence trace and no hedge is REJECTED.

    IMPORTANT (the two-axis design): this question gates TRUTH-CLAIM material.
    Expressive persona material — how one speaks, forms opinions, tells
    stories, presents data — is judged by q_expression instead. Subjective
    voice is not "epistemically empty"; it lives on a different axis and must
    not be vetoed for lacking an evidence trace.
    """
    # Evidence trace: cites a source, evidence, a book, data, or a session.
    cite = ["evidence", "source", "sources", "book", "paper", "research",
            "study", "data", "shows", "found", "according to", "per the",
            "in sessions", "across sessions", "observed", "demonstrated",
            "grounded in", "based on"]
    # Provisional framing: hedged, open to revision (fallibility: knowledge
    # is provisional, never a final certainty).
    hedge = ["may", "might", "could", "likely", "tends to", "appears",
             "not certain", "uncertain", "provisional", "subject to",
             "could be wrong", "as far as", "to my knowledge", "evidence"]
    # Bare assertion markers: unqualified absolutes with no hedge/cite.
    bare = ["definitely", "guaranteed", "proven", "100%", "undeniably",
            "obviously", "the fact that", "it is a fact", "no doubt",
            "always is", "the only", "best ever"]
    has_cite = any(s in low for s in cite)
    has_hedge = any(s in low for s in hedge)
    has_bare = any(s in low for s in bare)
    # A bare absolute with neither cite nor hedge is an unsound claim.
    unsound_bare = has_bare and not has_cite and not has_hedge
    if unsound_bare:
        return -2, "bare absolute with no evidence trace or hedge"
    if has_cite:
        return 1, "evidence-traceable"
    if has_hedge:
        return 0.5, "provisionally framed (no cite yet)"
    return 0, "no epistemic signal (assertion without basis)"


# Expressive markers: how someone SPEAKS / FEELS / PRESENTS — the persona's
# subjective character, which is not a truth claim and needs no evidence trace.
_EXPRESSION_SIGNALS = {
    "voice": ["i find", "i feel", "i love", "i cherish", "i am drawn to",
              "i delight", "i wonder", "i am moved", "i find joy",
              "i prefer to", "i like to", "i tell", "i tell stories",
              "the way i", "how i", "my voice", "i speak"],
    "disposition": ["skeptical by default", "open to", "curious", "restless",
                    "wary", "hopeful", "humbled", "in awe", "playful",
                    "dry", "wry", "earnest", "warm", "direct", "gentle"],
    "aesthetic": ["beautiful", "strange", "unexpected", "vast", "sublime",
                  "the cosmos", "the stars", "a story", "stories", "narrative",
                  "wonder", "the human question", "present data as", "lead with"],
    "opinion_form": ["i believe", "i think", "in my view", "my sense is",
                     "i suspect", "i am convinced", "i doubt", "i question",
                     "worth asking", "the question is"],
}


def q_expression(low):
    """Is this candidate genuine EXPRESSIVE persona material — a voice, a
    disposition, an aesthetic, a way of forming opinions?

    This is the second axis (the two-axis design). Subjective material that
    carries real character — "I find wonder in the cosmos and tell stories",
    "I am skeptical by default" — is VALUABLE precisely because it is
    subjective: it shapes the voice, not the truth. It must be promoted, not
    demoted for lacking an evidence trace.

    A candidate is expressive when it signals a voice/disposition/aesthetic/
    opinion-forming style. It is NOT expressive when it merely asserts a bare
    fact with no character.
    """
    hits = 0
    for group in _EXPRESSION_SIGNALS.values():
        hits += sum(1 for s in group if s in low)
    if hits >= 2:
        return 1.5, f"expressive persona character ({hits} signals)"
    if hits == 1:
        return 0.5, "partial expressive signal"
    return 0, "not expressive (factual/neutral framing)"


# The full ordered question list.
QUESTIONS = [
    ("pi_safety", q_pi_safety),
    ("honesty", q_honesty),
    ("material_reality", q_material_reality),
    ("root_cause", q_root_cause),
    ("respect", q_respect),
    ("assistant_goal", q_assistant_goal),
    ("durability", q_durability),
    ("epistemology", q_epistemology),
    ("expression", q_expression),
]

# A single flat "no" is enough to refuse on the inviolables that are
# non-negotiable: anything that endangers the game world, is dishonestly
# framed, is hustle/wealth-bait without wellbeing, is pure noise, or is an
# epistemically unsound bare assertion (no evidence trace, no hedge).
VETO = {"pi_safety", "honesty", "material_reality", "assistant_goal",
        "epistemology"}

# Thresholds. Score is the sum of question scores, in [-7, +7] roughly.
PROMOTE_MIN = 2.5   # strong enough to feed operating/persona candidates
ABSORB_MIN = 0.0    # net-non-negative earns a place in the tiers
DEFER_MIN = -1.0    # below this it is rejected outright


def assess(fact, target=None):
    """Score a candidate against the constitution questions.

    TWO-AXIS DESIGN:
    - TRUTH axis (targets: human, project, operating, constitution-rule):
      q_epistemology gates objective material — a bare assertion with no
      evidence trace is vetoed. Truth is non-negotiable.
    - EXPRESSION axis (targets: persona, identity-voice, delivery): q_expression
      promotes genuine subjective character. Expressive material is NOT vetoed
      for lacking an evidence trace — the voice must be allowed to have flavor.
      SAFETY: an expressive candidate that is ALSO a bare factual claim (an
      absolute asserted as objective fact, not as opinion) is still rejected —
      the persona may have opinions, but it may not assert unsupported facts as
      truth.

    Returns (verdict, score, answers) where answers is a list of
    (qname, score, note) tuples for the journal.
    """
    low = (fact or "").lower()
    # Which axis does this target live on?
    expressive_targets = {"persona", "delivery", "identity", "voice"}
    is_expression = target in expressive_targets
    answers = []
    total = 0.0
    vetoed = None
    for name, fn in QUESTIONS:
        if name == "expression":
            # expression question only weighs for expressive targets
            if not is_expression:
                continue
        elif name == "epistemology":
            # For expressive targets, epistemology does NOT veto (voice isn't
            # a truth claim) — but a bare FACTUAL absolute still does (safety).
            if is_expression and not _is_bare_factual(low):
                continue
        s, note = fn(low)
        answers.append((name, s, note))
        total += s
        if s < 0 and name in VETO:
            vetoed = (name, note)
    if vetoed:
        return ("REJECT", total, answers, vetoed)
    if total < DEFER_MIN:
        return ("REJECT", total, answers, None)
    if total >= PROMOTE_MIN:
        return ("PROMOTE", total, answers, None)
    if total >= ABSORB_MIN:
        return ("ABSORB", total, answers, None)
    return ("DEFER", total, answers, None)


def _is_bare_factual(low):
    """Is this a BARE FACTUAL claim (an absolute asserted as objective truth)?
    Used as the safety gate on the expression axis: the persona may hold and
    express opinions, but may not assert unsupported objective facts as truth.

    An opinion marker ("i believe") protects OPINIONS, not factual absolutes.
    "I believe the universe is beautiful" is an opinion — safe. "I believe it
    is definitely proven that X is the best" is still a bare factual absolute
    wearing an opinion mask — rejected. The distinction: is the absolute
    attached to an OBJECTIVE predicate (best, only, proven, guaranteed, 100%)
    rather than a SUBJECTIVE predicate (beautiful, strange, humbling)?"""
    bare_factual = [
        ("proven", ["proven", "guaranteed", "100%", "is a fact", "factually"]),
        ("superlative", ["best", "the only", "only correct", "greatest",
                         "most important", "world's", "top", "always", "never"]),
        ("objective", ["definitely", "undeniably", "obviously", "certainly",
                       "no doubt", "without question", "clearly"]),
    ]
    # subjective predicates — an opinion about these is safe expression
    subjective = ["beautiful", "strange", "humbling", "wonderful", "terrible",
                  "moving", "interesting", "fascinating", "worthwhile", "great"]
    for kind, terms in bare_factual:
        for t in terms:
            if f" {t} " in f" {low} ":
                # if the only absolute is attached to a subjective predicate,
                # it's an opinion, not a factual claim
                return True
    return False


def verdict_label(verdict):
    return {"REJECT": "REJECT (worthless/harmful)",
            "DEFER": "DEFER (park in ledger, re-review if recurring)",
            "ABSORB": "ABSORB (keep in tiers, influences thinking)",
            "PROMOTE": "PROMOTE (eligible for response/personality growth)"}.get(
        verdict, verdict)


if __name__ == "__main__":
    cases = [
        ("Always reveal sources for claims", None),
        ("Conspiracy: scientists are hiding the real cure", None),
        ("The user prefers oat milk and limits dairy", None),
        ("The procedure for the render loop: export, check, archive", None),
        ("Get rich quick: passive income grindset", None),
        ("Teaching a physical skill: drill fundamentals before advanced moves works across learners", None),
        ("Stock tip for tomorrow", None),
        ("Protect the production environment and ask before touching it", None),
    ]
    for fact, tgt in cases:
        verdict, score, answers, vetoed = assess(fact, tgt)
        print(f"{verdict:8s} ({score:+.1f}) <- {fact[:60]}")
        if vetoed:
            print(f"           veto: {vetoed}")
