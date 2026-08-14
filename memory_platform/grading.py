"""Graded importance routing for the unified memory model.

Grades how a fact/rule should be placed across the tiers:

    G0  constitution   — inviolable, always-followed rules. read_only to the
                         curator; added ONLY via an explicit user directive
                         ("always do X" / "add this to the constitution").
    G1  operating      — how the user wants the agent to behave (fast, auto).
    G2  human/project  — durable facts about the user / active project state.
    G3  hindsight      — warm ideas, facts to be recalled, not constantly present.
    G4  mempalace      — cold archival: documents, transcripts, verbatim content.

Grading is heuristic + keyword-scored (local, free, batch at sleep-time — no
LLM per fact). It feeds the curator's plan_ops so every evidence item lands in
the tier where it belongs instead of being dumped into a block.

Portable: no user name, city, or domain is hardcoded. The user's name is
matched only via the optional MEMORY_USER_NAME env var.
"""

import os

# --- Imperative / rule signals: a sentence instructing HOW to operate. ------
RULE_WEAK = [
    "should", "prefer", "instead of", "lead with", "first", "before ",
    "avoid", "favour", "keep ", "stop ", "start ", "use ",
]
RULE_STRONG = ["always", "never ", "must", "do not", "don't", "i always",
               "i must", "i should"]
# Positive-affirmation hint words — behavioural, not factual.
AFFIRM_SIGNALS = [
    "i protect", "i am", "i reveal", "i factor", "i dissolve", "i respect",
    "i teach", "i make", "i write", "i use the tiers", "i check", "i stop",
]

# --- Subject signals: who/what the fact is about. ---------------------------
# PORTABLE: no person's name, city, or pet is hardcoded. The subject is "the
# user" when the fact concerns the person running the system (personal
# attributes, preferences, biography) — detected by generic personal-fact
# signals, not by a hardcoded identity. The user's name is matched via
# MEMORY_USER_NAME (optional; absent = no name-based routing).
USER_FACT_SIGNALS = [
    "name", "email", "role", "lives in", "located in", "daw", "os:",
    "shell", "has a dog", "has a cat", "prefers", "works in", "works at",
    "teaches", "studies", "interested in", "financial", "income", "dietary",
    "location", "my home", "my machine",
]
# Decisive personal-attribute signals: alone, they mark a fact as about the
# user ("prefers X", "lives in Y") — no second signal needed.
USER_ATTR_DECISIVE = ["prefers", "lives in", "located in", "teaches",
                      "studies", "works at", "works in", "interested in",
                      "dietary", "income", "financial"]
_user_name = (os.environ.get("MEMORY_USER_NAME") or "").strip().lower()
PROJECT_SIGNALS = [
    "project", "current work", "task", "todo", "goal", "milestone", "deadline",
    "in progress", "blocked", "decision", "spec", "wip", "building ", "developing ",
    "course", "channel", "migration", "feature", "fixing", "working on",
    "active projects", "current state",
]

# --- Directives that grow the constitution. ----------------------------------
CONSTITUTION_SIGNALS = [
    "add this to the constitution", "constitution", "always do",
    "i will always", "must always", "inviolable",
]

# Block the grader would never route straight to a hot block.
DOC_SIGNALS = ["book", "chapter", "document", "transcript", "pdf", "epub",
               "verbatim", "diary entry", "palace drawer", "gutenberg"]


# Curated prohibition -> true positive affirmation. A blind "never X -> always
# X" rewrite is dangerous (it produces the OPPOSITE of the intent). So the
# automated transform only touches rules whose positive form is known for sure;
# anything else is passed through unchanged and left to the agent's deliberate
# constitution-growth path (where the user's exact wording is available).
AFFIRM_DICT = {
    "never defend billionaires": "I prioritise collective wellbeing and factual, caring explanations",
    "don't defend billionaires": "I prioritise collective wellbeing and factual, caring explanations",
    "never equate socialism with big government": "I describe socialism accurately, on its own terms",
    "never offer rigid plans": "I offer three gentle options when direction is sought",
    "don't offer rigid plans": "I offer three gentle options when direction is sought",
    "never hedge": "I answer directly, without hedging",
    "don't hedge": "I answer directly, without hedging",
    "do not overresearch": "I act on the quick answer when one will do",
    "don't overresearch": "I act on the quick answer when one will do",
    "do not overclaim": "I name evidence as evidence and confirmed causes as confirmed",
    "never overclaim": "I name evidence as evidence and confirmed causes as confirmed",
    "do not stall": "I act decisively once the root cause is understood",
    "don't stall": "I act decisively once the root cause is understood",
    "do not touch the raspberry pi": "I protect the Raspberry Pi's Foundry world and ask before touching it",
    "never touch the raspberry pi": "I protect the Raspberry Pi's Foundry world and ask before touching it",
    "ask permission before modifying anything on the pi": "I check with the user before touching anything on the Pi",
    "do not use capitalistic frameworks": "I use factual, material, caring, non-blaming explanations",
    "never use hustle frameworks": "I use factual, material, caring, non-blaming explanations",
    "do not offer plans": "I offer three gentle options when direction is sought",
    "don't offer plans": "I offer three gentle options when direction is sought",
    "do not search endlessly": "I stop, research properly, and find the root cause",
    "don't iterate endlessly": "I stop, research properly, and find the root cause",
}


def affirm_rule(fact):
    """Rewrite a perceived demand/rule as a positive affirmation.

    Only rewrites are applied when the positive form is known for sure
    (AFFIRM_DICT); a blind string substitution can invert meaning. Unknown
    prohibitions pass through unchanged — the agent's deliberate recording
    path handles those with the original wording in hand.
    """
    f = fact.strip().lstrip("-* ").strip()
    low = " ".join(f.lower().split())
    for key, pos in AFFIRM_DICT.items():
        if key in low or key in low.strip(".").replace("  ", " "):
            return pos
    return f


def grade_fact(fact, target=None):
    """Return the tier (G0..G4) for a fact/rule based on type + language.

    `target` is the coarse target the evidence extractor already guessed
    (human/operating/project/persona). Grading refines where within the tiers
    it actually belongs — the granularity the user asked for.
    """
    low = fact.lower()
    # Constitution grows only via explicit directive language.
    if any(s in low for s in CONSTITUTION_SIGNALS):
        return "constitution"
    # Documents / verbatim archival material -> cold.
    if any(s in low for s in DOC_SIGNALS):
        return "mempalace"
    # Behavioural rules -> operating (hot, auto). Imperative + agent-first.
    rule_score = sum(1 for s in RULE_STRONG if s in low) * 2 + sum(
        1 for s in RULE_WEAK if s in low)
    affirm_score = sum(1 for s in AFFIRM_SIGNALS if s in low)
    user_score = sum(1 for s in USER_FACT_SIGNALS if s in low)
    proj_score = sum(1 for s in PROJECT_SIGNALS if s in low)
    if target == "operating":
        return "operating"
    if target == "persona":
        # persona is identity growth — a tier of its own, slow-gated.
        return "persona"
    # A user fact states a stable attribute/preference about the user; a rule
    # states what the agent does. "User prefers X" -> human. "When the user
    # says X, do Y" -> operating. Strong imperative on the agent's behaviour
    # -> operating. A decisive personal attribute routes to human even alone;
    # otherwise the general user-signal count decides.
    name_token = f" {_user_name} " if _user_name else "__none__"
    decisive_attr = any(s in low for s in USER_ATTR_DECISIVE)
    is_personal_attr = (decisive_attr or user_score >= 2) and not any(
        s in low for s in ("i ", "do not", "always", "never ")) and \
        name_token not in f" {low} "
    if is_personal_attr:
        return "human"
    if rule_score >= 2 or affirm_score >= 1:
        return "operating"
    if target == "project" or proj_score >= 2:
        return "project"
    if user_score >= 2 or target == "human":
        return "human"
    if target == "skill" or "procedure" in low or "steps" in low:
        return "skill"
    return "hindsight"


def tier_of(grade):
    """Human label for a grade, for journaling / canary output."""
    return {
        "constitution": "G0",
        "operating": "G1",
        "persona": "G2p",
        "human": "G2",
        "project": "G2",
        "skill": "P",
        "mempalace": "G4",
        "hindsight": "G3",
    }.get(grade, "G3")


if __name__ == "__main__":
    # quick self-check
    cases = [
        ("The user prefers oat milk and limits dairy", None),
        ("When the user says diagnose and fix, research the root cause first", None),
        ("Always reveal sources for claims", None),
        ("Never touch the production server without permission", None),
        ("Add this to the constitution", None),
        ("The transcript of session 2026-08-12 is archived", None),
        ("Building a beginner course and a channel for it", None),
    ]
    for fact, tgt in cases:
        print(f"{grade_fact(fact, tgt):10s} <- {fact}")