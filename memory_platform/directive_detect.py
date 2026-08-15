#!/usr/bin/env python3
"""directive_detect.py — auto-detect explicit constitutional directives in user text.

Closes the manual-step gap: the constitution grows via constitution-add.py, but
nothing previously *triggered* that path automatically. This detector watches
user messages for explicit directive language and flags them so the agent is
reminded to record the affirmation (via constitution-add.py) — the trigger is
automatic; the affirmation crafting stays with the agent (it needs real
understanding, not keyword substitution).

Usage:
    directive_detect.py "<user text>"        # JSON: {directive: bool, signals: [...]}
"""

import json
import re
import sys

# Explicit directive language (matches grading.py's CONSTITUTION_SIGNALS, plus
# the imperative phrasing the user actually uses in conversation).
STRONG_SIGNALS = [
    "add this to the constitution",
    "constitutionally core",
    "constitutional core",
    "should be constitutionally",
    "should be a constitution",
    "make it a constitution",
    "as a constitutional thing",
    "constitutional thing is",
    "constitutional rule",
    "always do this",
    "always do that",
    "i will always",
    "must always",
    "from now on. always",
    "always do x",
    "that should be constitutionally",
    "that should be constitutional",
    "needs to be constitutional",
]

# "always do X" pattern — the general form the user uses for standing rules.
ALWAYS_RE = re.compile(r"\balways\s+(do|use|make|hold|keep|give|tell|remember|weigh|treat|build|apply|automate|translate|offer|write|reveal|factor)\b")
NEVER_RE = re.compile(r"\b(never|do not|don't)\s+(do|use|make|touch|ignore|skip|round|offer|build)\b", re.I)


def detect(text: str) -> dict:
    low = " ".join(text.lower().split())
    hits = [s for s in STRONG_SIGNALS if s in low]
    if not hits and ALWAYS_RE.search(low):
        hits.append("always-<verb> (standing rule)")
    if not hits and NEVER_RE.search(low):
        hits.append("never/do-not (standing rule)")
    return {
        "directive": bool(hits),
        "signals": hits[:6],
        "text": text[:200],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"directive": False, "signals": [], "text": ""}))
        sys.exit(0)
    print(json.dumps(detect(sys.argv[1])))
