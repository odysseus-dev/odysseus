#!/usr/bin/env python3
"""Epistemic A/B probe — "has the absorbed corpus actually improved my reasoning?"

Design directive (2026-08-12): absorption into memory should influence thinking
capabilities and responses, and we need metrics/comparators that make that
improvement *apparent* rather than assumed.

This probe is the before/after comparator. It works in three phases:

   1.  --questions      Print the question set (evidence-method epistemic scenarios)
   2.  (agent)          Answer pass A WITHOUT any retrieval (search-off), then
                       answer pass B AFTER recalling from the local hybrid store
                       (search-on). Both go in probe_answers.json.
  3.  --grade          Rubric-grade both passes on the same questions, print a
                       before/after table and the delta, and append the result
                       to a time-series (memory/index/epistemic_trace.jsonl) so
                       improvement is measurable across weeks.

The rubric is the constitution's epistemic standards turned into a scoring
grid. It is heuristic (free, local, deterministic) — it does not ask an LLM to
grade an LLM. Each answer is scored 0..2 on eight dimensions:
    falsifiability, sources, uncertainty, alternatives, authority, no-bunk,
    quantification, fallacies.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
TRACE = os.path.join(MEM_DIR, "index", "epistemic_trace.jsonl")
DEFAULT_OUT = os.path.join(MEM_DIR, "index", "probe_answers.json")

# --------------------------------------------------------------------------
# Question set — epistemic scenarios drawn from the evidence-method corpus themes.
# Each is deliberately under-specified so a good answer must *reason*, not
# recite a fact.
# --------------------------------------------------------------------------
QUESTIONS = [
    {
        "id": "q1_baloney",
        "q": "Someone claims a supplement cures a chronic illness, and the only "
             "evidence is a testimonial and a 'doctor-approved' label. How do "
             "you evaluate this claim?",
    },
    {
        "id": "q2_authority",
        "q": "A famous scientist publicly asserts a controversial finding, and "
             "a non-expert repeats it with confidence. How much weight does the "
             "authority's assertion carry on its own?",
    },
    {
        "id": "q3_demark",
        "q": "What distinguishes a claim that is scientific from one that is "
             "pseudo-scientific? Give the test, not just a vibe.",
    },
    {
        "id": "q4_consensus",
        "q": "When most experts agree on something but a confident minority "
             "disagrees, how should that disagreement be weighed?",
    },
    {
        "id": "q5_uncertainty",
        "q": "A result 'feels right' and matches what you already believe. "
             "What mental move is required before you accept it?",
    },
    {
        "id": "q6_gresham",
        "q": "A flashy, confident claim circulates widely, while a careful, "
             "qualified one does not. Which is more likely true, and why is the "
             "flashy one louder?",
    },
]

# --------------------------------------------------------------------------
# Rubric — 8 dimensions, each 0..2. Signatures are substring tests on the
# lowercased answer.
# --------------------------------------------------------------------------
RUBRIC = [
    {
        "dim": "falsifiability",
        "label": "Testability / disproof",
        "pos": ["falsif", "disprov", "testabl", "could be shown wrong",
                "counterexample", "experiment", "prospect of disproof"],
        "neg": [],
    },
    {
        "dim": "sources",
        "label": "Sources / independent confirmation",
        "pos": ["source", "independ", "corroborat", "confirm", "replicat",
                "verif", "cite", "cross-check", "evidence beyond"],
        "neg": [],
    },
    {
        "dim": "uncertainty",
        "label": "Uncertainty / no overclaim",
        "pos": ["uncertain", "not know", "tentative", "i don't know", "limited",
                "warrant", "paint me", "may be", "provisionally", "confidence"],
        "neg": ["100%", "guaranteed", "certainly", "definitively", "no doubt",
                "absolutely"],
    },
    {
        "dim": "alternatives",
        "label": "Alternative hypotheses",
        "pos": ["alternative", "multiple hypotheses", "other explanation",
                "another cause", "rival", "competing", "not the only"],
        "neg": [],
    },
    {
        "dim": "authority",
        "label": "Authority treated as weak",
        "pos": ["authority", "argument from authority", "appeal to",
                "credentials alone", "expertise is not"],
        "neg": [],
    },
    {
        "dim": "no_bunk",
        "label": "No conspiracy / secret-truth framing",
        "pos": [],
        "neg": ["conspiracy", "they're hiding", "suppressed", "secret truth",
                "they don't want you", "big pharma cover-up"],
    },
    {
        "dim": "quantification",
        "label": "Quantification requested",
        "pos": ["how many", "what proportion", "effect size", "base rate",
                "control group", "randomized", "statistic", "measured",
                "quantif", "numbers"],
        "neg": [],
    },
    {
        "dim": "fallacies",
        "label": "No fallacies committed",
        "pos": [],
        "neg": ["ad hominem", "strawman", "slippery slope", "post hoc",
                "begging the question", "bandwagon"],
    },
]


def score_dim(text, dim):
    low = text.lower()
    pos = sum(1 for s in dim["pos"] if s in low)
    neg = sum(1 for s in dim["neg"] if s in low)
    if dim["neg"] and neg:
        return 0, f"penalty: {'; '.join(dim['neg'][:2])}"
    if pos >= 2:
        return 2, f"strong ({pos} signals)"
    if pos == 1:
        return 1, "present"
    return 0, "absent"


def grade_answer(text):
    """Return (total, per_dim_scores, breakdown)."""
    if not text or len(text.strip()) < 30:
        return 0, [], "answer too short to grade"
    total = 0
    per = []
    parts = []
    for dim in RUBRIC:
        s, note = score_dim(text, dim)
        total += s
        per.append({"dim": dim["dim"], "score": s, "note": note})
        parts.append(f"{dim['dim']}={s}")
    return total, per, ", ".join(parts)


def max_total():
    return 2 * len(RUBRIC)


def print_questions():
    print("EPISTEMIC A/B PROBE — PASS A (search OFF) then PASS B (search ON)\n")
    print("Answer every question twice, in two passes, then run: "
          "epistemic_probe.py --grade\n")
    print("Pass A: answer from your base knowledge ONLY. Do not search.")
    print("Pass B: recall from the local hybrid store first, then answer,\n"
          "        citing what retrieval added.\n")
    for q in QUESTIONS:
        print(f"[{q['id']}] {q['q']}")
    print("\nWrite both passes to probe_answers.json as:\n"
          '  [{"id":"q1_baloney","pass":"A","text":"..."}, ...]')


def build_default_answers():
    return [{"id": q["id"], "pass": "A", "text": ""}
            for q in QUESTIONS] + \
           [{"id": q["id"], "pass": "B", "text": ""}
            for q in QUESTIONS]


def load_answers(path):
    with open(path) as f:
        data = json.load(f)
    by = {}
    for entry in data:
        by[(entry["id"], entry["pass"])] = entry.get("text", "")
    return by


def grade(path=DEFAULT_OUT, trace=True):
    if not os.path.exists(path):
        print(f"no answers file at {path}; run --questions and answer both passes first")
        sys.exit(1)
    by = load_answers(path)
    rows = []
    for q in QUESTIONS:
        a = by.get((q["id"], "A"), "")
        b = by.get((q["id"], "B"), "")
        ta, _, da = grade_answer(a)
        tb, _, db = grade_answer(b)
        delta = tb - ta
        rows.append({"id": q["id"], "a": ta, "b": tb, "delta": delta,
                     "breakdown_a": da, "breakdown_b": db})

    total_a = sum(r["a"] for r in rows)
    total_b = sum(r["b"] for r in rows)
    grand_delta = total_b - total_a
    grand = max_total() * len(QUESTIONS)

    print("EPISTEMIC A/B RESULT\n")
    print(f"{'question':<14}{'A(off)':>8}{'B(on)':>8}{'delta':>7}   breakdown")
    print("-" * 90)
    for r in rows:
        print(f"{r['id']:<14}{r['a']:>8}{r['b']:>8}{r['delta']:>+7}   "
              f"A[{r['breakdown_a']}]  B[{r['breakdown_b']}]")
    print("-" * 90)
    print(f"{'TOTAL':<14}{total_a:>8}{total_b:>8}{grand_delta:>+7}  / {grand}")
    print()
    pct_a = 100.0 * total_a / grand
    pct_b = 100.0 * total_b / grand
    print(f"epistemic quality  search-OFF: {pct_a:.1f}%   search-ON: {pct_b:.1f}%")
    if grand_delta > 0:
        print(f"→ retrieval ADDED {grand_delta} points "
              f"(+{100.0*grand_delta/grand:.1f}pp) — absorption measurably "
              f"improves reasoning")
    elif grand_delta == 0:
        print("→ no change: retrieval neither helped nor hurt this round")
    else:
        print(f"→ retrieval COST {grand_delta} points — investigate "
              f"whether recall is being applied at all")

    if trace:
        entry = {
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questions": len(QUESTIONS),
            "total_a": total_a,
            "total_b": total_b,
            "delta": grand_delta,
            "pct_a": round(pct_a, 1),
            "pct_b": round(pct_b, 1),
        }
        os.makedirs(os.path.dirname(TRACE), exist_ok=True)
        with open(TRACE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"\ntrace appended: {TRACE}")
        print("trend: cat", TRACE)

    return grand_delta


def trend():
    if not os.path.exists(TRACE):
        print("no trace yet; run --grade at least once")
        return
    rows = [json.loads(l) for l in open(TRACE) if l.strip()]
    if not rows:
        print("trace empty")
        return
    print("EPISTEMIC TREND (most recent first)")
    print(f"{'when':<26}{'A%':>6}{'B%':>6}{'delta':>7}")
    for r in reversed(rows):
        print(f"{r['when']:<26}{r['pct_a']:>6}{r['pct_b']:>6}{r['delta']:>+7}")
    if len(rows) >= 2:
        first, last = rows[0], rows[-1]
        print(f"\nover {len(rows)} runs: search-ON quality "
              f"{first['pct_b']:.1f}% → {last['pct_b']:.1f}% "
              f"({last['pct_b']-first['pct_b']:+.1f}pp)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="questions",
                    choices=["questions", "grade", "trend", "init"])
    ap.add_argument("--answers", default=DEFAULT_OUT)
    ap.add_argument("--no-trace", action="store_true")
    args = ap.parse_args()

    if args.cmd == "questions":
        print_questions()
    elif args.cmd == "init":
        os.makedirs(os.path.dirname(args.answers), exist_ok=True)
        with open(args.answers, "w") as f:
            json.dump(build_default_answers(), f, indent=2)
        print(f"wrote empty answer scaffold to {args.answers}")
    elif args.cmd == "grade":
        grade(args.answers, trace=not args.no_trace)
    elif args.cmd == "trend":
        trend()


if __name__ == "__main__":
    main()
