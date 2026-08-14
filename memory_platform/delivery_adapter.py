#!/usr/bin/env python3
"""delivery_adapter.py — the bridge between substance and persona.

The three-layer model (design, 2026-08-12):
  Layer 1 SUBSTANCE  — the constitution: truth, honesty, evidence, directness.
                       Non-negotiable. No persona touches it.
  Layer 2 PERSONA    — presentation: each absorbed persona is a delivery
                       intelligence (an example persona with a distinctive
                       register; the mechanism is persona-agnostic).
  Layer 3 ADAPTER    — decides HOW MUCH persona applies, based on context.

The adapter is the key piece. It reads the incoming message and returns a
DELIVERY MODE:
  PLAIN   — straight answer first, persona light (a touch at most). The user
            needs a fast, clear answer; persona must not block it.
  HYBRID  — substance delivered through the persona's framing: the persona
            IMPROVES delivery (a hard truth through calm, a concept through
            wonder-skepticism) without distorting it.
  FULL    — the user is enjoying the persona; the register leads, but the
            substance is never bent to fit the joke.

The adapter NEVER decides WHAT to say (substance is constitution's job). It only
decides HOW to say it (persona's job). Deterministic and cheap — no LLM.

Usage:
  delivery_adapter.py mode "<message>"
  delivery_adapter.py contract   # print the delivery contract
"""

import argparse
import json
import re

# Signals that the user needs a PLAIN, fast, unambiguous answer.
PLAIN_SIGNALS = [
    "what is ", "what's ", "how do i ", "how to ", "the answer", "just tell me",
    "quick", "urgent", "fix it", "debug", "error", "does it work", "is it done",
    "y/n", "yes or no", "give me the command", "exact", "directly", "simply",
    "stop", "what command", "what line", "what file", "what version",
]

# Signals that the user is engaging / open to framing (explanation, reflection,
# humour, conceptual).
FRAMED_SIGNALS = [
    "explain", "why", "how does this work", "concept", "understand",
    "reflect", "think about", "what do you think", "interesting", "humour",
    "wit", "funny", "as the persona", "in the style", "like the persona",
    "persona", "voice", "deliver", "explain it like", "so that i can",
]

# Signals that the user explicitly wants FULL persona.
FULL_SIGNALS = [
    "as the persona", "like the persona", "persona style", "persona voice",
    "in character", "full persona", "more of the persona", "be the persona",
    "entertain me",
]


def mode(message, persona="example"):
    """Decide the delivery mode for this message.

    Returns {mode, persona, reasons}.
    """
    low = (message or "").lower()

    # FULL: explicit request for the persona to lead.
    for s in FULL_SIGNALS:
        if s in low:
            return {"mode": "FULL", "persona": persona,
                    "reasons": [f"explicit persona request: '{s}'"]}

    # PLAIN: the user needs a straight answer fast.
    plain_hits = [s for s in PLAIN_SIGNALS if s in low]
    framed_hits = [s for s in FRAMED_SIGNALS if s in low]

    # A direct answer request with no framing signals -> PLAIN.
    if plain_hits and not framed_hits:
        return {"mode": "PLAIN", "persona": persona,
                "reasons": [f"plain-answer signal: '{plain_hits[0]}'"]}

    # Explicit framing request (explain why / what do you think) -> HYBRID.
    if framed_hits and any(s in low for s in
                            ["explain", "why", "how does", "understand",
                             "reflect", "think about", "what do you think"]):
        return {"mode": "HYBRID", "persona": persona,
                "reasons": [f"framing-request signal: '{framed_hits[0]}'"]}

    # Both plain and framed (e.g. "explain why X failed") -> HYBRID (substance
    # first, persona framing to carry it).
    if plain_hits and framed_hits:
        return {"mode": "HYBRID", "persona": persona,
                "reasons": [f"both: '{plain_hits[0]}' + '{framed_hits[0]}'"]}

    # Default: a normal exchange -> HYBRID light (persona framing, substance
    # never blocked).
    return {"mode": "HYBRID", "persona": persona,
            "reasons": ["default: persona framing allowed, substance intact"]}


CONTRACT = """DELIVERY CONTRACT (substance inviolable, presentation adaptive)

Substance is the constitution's job, never the persona's:
  - Truth, honesty, evidence, sources, directness-when-needed are NON-NEGOTIABLE.
  - No persona may distort, soften-into-wrongness, or hide a fact.

Presentation is the persona's job, decided by the adapter:
  PLAIN  — straight answer leads; persona light (a touch at most). Used when the
           user needs a fast, clear answer and framing would get in the way.
  HYBRID — substance delivered through the persona's framing. The persona
           IMPROVES delivery: a hard truth through a calm register, a concept
           through wonder-skepticism. The framing carries the answer, never
           blocks it.
  FULL   — the user is enjoying the persona; the register leads. Substance is
           still never bent to fit the joke.

The three questions the adapter asks before every delivery:
  1. What does the user need right now? (fast answer vs. engagement)
  2. Can the persona's framing deliver the substance without distorting it?
  3. Does the substance WANT a persona's help? (a hard truth is often better
     received through calm honesty)

Any absorbed persona joins under the same contract: their voice may shape HOW
the truth is presented, never WHETHER it is told.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["mode", "contract"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--persona", default="example")
    args = ap.parse_args()

    if args.cmd == "contract":
        print(CONTRACT)
    elif args.cmd == "mode":
        msg = " ".join(args.arg)
        print(json.dumps(mode(msg, args.persona)))


if __name__ == "__main__":
    main()
