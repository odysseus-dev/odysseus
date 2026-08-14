#!/usr/bin/env python3
"""CITED trace — "when does absorbed material actually change my output?"

Design directive (2026-08-12): absorption into memory should influence thinking
and responses, and we need metrics that make that influence *apparent*. This is
the third comparator: a frequency/usage trace.

It scans recent session transcripts for the fingerprints of absorbed-corpus
influence — the exact phrases and source references that only appear when the
agent is drawing on a mined corpus drawer rather than its base training. Every
hit is journaled as a CITED event, and a running tally is kept so we can see,
over weeks, whether the corpus is changing output (real influence) or just
sitting in cold storage (decoration).

Fingerprints are intentionally precise: the method's own vocabulary — the
baloney-detection terms, the burden-of-skepticism framing, the wonder-and-
skepticism balance — with an epistemic verb. A vague echo of an idea (which
could come from training) is NOT counted. Only a nameable, attributable
reference counts — that is the honest bar.
"""

import argparse
import glob
import json
import os
import re
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
TRANSCRIPTS_DIR = os.path.join(MEM_DIR, "transcripts")
JOURNAL_DIR = os.path.join(MEM_DIR, "journal")
TALLY = os.path.join(MEM_DIR, "index", "cited_tally.json")

# Fingerprints of attributable corpus influence. Format: (regex, source label).
# Regex is case-insensitive; only matched as whole phrases, not substrings of
# unrelated words.
FINGERPRINTS = [
    (r"baloney detection kit", "corpus: evidence-detection method"),
    (r"burden of skepticism", "corpus: skepticism burden"),
    (r"fine art of baloney detection", "corpus: evidence-detection method ch.12"),
    (r"extraordinary claims require extraordinary evidence",
     "corpus: extraordinary-claims principle"),
    (r"wonder[ -]skepticism", "corpus: wonder-skepticism balance"),
    (r"falsifiability", "corpus: falsifiability principle"),
    (r"authority cargo", "corpus: authority-cargo rejection"),
    (r"fallibility|knowledge is provisional", "corpus: provisional-knowledge framing"),
    (r"from the palace|palace drawer", "mempalace retrieval"),
    (r"from the corpus|mined corpus", "absorbed corpus"),
]

# Procedural scaffolding that marks a turn as operational chatter, not
# reasoning influenced by the corpus. If a hit lands inside such a turn it is
# not counted — a debug note about a file is not the corpus shaping thought.
PROCEDURAL = [
    r"let me (check|debug|look|find|see|fix|locate|test)",
    r"let's (check|debug|look|find|see|fix|locate|test)",
    r"why it (failed|skipped|was skipped)",
    r"check the skipped",
    r"i need to (check|debug|find|locate|mine)",
    r"grep|ls -|head -|tail -",
    r"extract|epub|pdf|convert|file encoding",
    r"fingerprint|tally|cite_trace|scan",
]


def is_procedural(turn):
    low = turn.lower()
    return any(re.search(p, low) for p in PROCEDURAL)


def fingerprint_matches(text):
    """Return list of (source_label) that text is attributable to."""
    hits = []
    low = text.lower()
    for pattern, label in FINGERPRINTS:
        if re.search(pattern, low):
            hits.append(label)
    return hits


def load_tally():
    if os.path.exists(TALLY):
        with open(TALLY) as f:
            return json.load(f)
    return {"events": [], "per_source": {}, "total": 0}


def save_tally(tally):
    os.makedirs(os.path.dirname(TALLY), exist_ok=True)
    with open(TALLY + ".tmp", "w") as f:
        json.dump(tally, f, indent=2)
    os.replace(TALLY + ".tmp", TALLY)


def journal(event):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = os.path.join(JOURNAL_DIR, f"{month}.md")
    with open(path, "a") as f:
        f.write(f"`{ts}` **CITED** → influence trace\n")
        f.write(f"  - source: {event['source']}\n")
        f.write(f"  - file: {event['file']}\n")
        f.write(f"  - quote: {event['quote'][:200]}\n\n")


def scan(transcripts_dir=TRANSCRIPTS_DIR, max_files=8, dry=False):
    """Scan the most recent transcripts; journal + tally any attributable hit."""
    files = sorted(glob.glob(os.path.join(transcripts_dir, "*.md")),
                   key=os.path.getmtime, reverse=True)
    tally = load_tally()
    seen_events = set(tally.get("events", []))
    hits = []
    new_events = 0
    for path in files[:max_files]:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # Only scan assistant-authored turns (after '### assistant'), where
        # output influence matters. Skip tool-output blocks for precision, and
        # skip procedural chatter (debug/scan/mine scaffolding) — that is not
        # the corpus shaping thought.
        for m in re.finditer(r"### assistant\n(.*?)(?=\n### |\Z)", text,
                             re.DOTALL):
            turn = m.group(1)
            if is_procedural(turn):
                continue
            for source in fingerprint_matches(turn):
                event = {
                    "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": source,
                    "file": os.path.basename(path),
                    "quote": _quote_context(turn, source),
                }
                key = f"{source}|{os.path.basename(path)}"
                if key in seen_events:
                    continue
                seen_events.add(key)
                new_events += 1
                tally["per_source"][source] = tally["per_source"].get(source, 0) + 1
                tally["total"] += 1
                tally["events"].append(key)
                hits.append(event)
                if not dry:
                    journal(event)
    save_tally(tally)
    return hits, tally


def _quote_context(turn, source_label):
    """Return a short snippet of the turn around the first fingerprint hit."""
    low = turn.lower()
    best = None
    for pattern, label in FINGERPRINTS:
        if label == source_label:
            m = re.search(pattern, low)
            if m:
                best = m
                break
    if not best:
        return turn[:200]
    start = max(0, best.start() - 60)
    return turn[start:best.end() + 60].replace("\n", " ")


def report(transcripts_dir=TRANSCRIPTS_DIR, max_files=8, dry=False):
    hits, tally = scan(transcripts_dir, max_files, dry)
    print("CITED TRACE")
    print(f"  scanned {max_files} most recent transcripts; "
          f"{len(hits)} new attributable hits")
    for h in hits:
        print(f"  CITED  [{h['source']}]  in {h['file']}")
        print(f"         “{h['quote']}”")
    if not hits:
        print("  (no new attributable corpus influence in this window — "
              "the corpus is sitting cold, not changing output)")
    print()
    print("  TALLY (all time):")
    for src, n in sorted(tally["per_source"].items(), key=lambda x: -x[1]):
        print(f"    {n:>4}x  {src}")
    print(f"    TOTAL {tally['total']} attributable citations")
    if tally["total"]:
        print("\n  interpretation: each CITED is a moment the absorbed corpus")
        print("  measurably changed an assistant output vs base training.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", default=TRANSCRIPTS_DIR)
    ap.add_argument("--max-files", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true",
                    help="scan + report without journaling")
    ap.add_argument("--reset", action="store_true",
                    help="clear the tally (new baseline)")
    args = ap.parse_args()
    if args.reset:
        save_tally({"events": [], "per_source": {}, "total": 0})
        print("tally reset")
        return
    report(args.transcripts, args.max_files, args.dry_run)


if __name__ == "__main__":
    main()
