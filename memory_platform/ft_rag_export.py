#!/usr/bin/env python3
"""ft_rag_export.py — turn the VERIFIED store into fine-tuning data (uplift #4).

The research finding (Bioengineering 2025, five models, MedQuAD): FT+RAG
consistently outperforms RAG alone AND fine-tuning alone. The system already
has the RAG half (hybrid recall); this exporter produces the FT half from the
system's unique asset — 533+ evidence-graded, verdict-tagged entries that NO
research corpus has. Fine-tuning a small local model on THIS (not raw scraped
text) is the FT+RAG uplift the literature recommends.

What it exports:
- instruction pairs from the politics wing: claim -> verdict -> evidence
  (the natural question/answer shape)
- constitution entries as behavioral instruction pairs
- a summary report (counts, verdict distribution, token estimate)

Usage:
    ft_rag_export.py export [--wing politics] [--out dir]
    ft_rag_export.py report
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path:
    sys.path.insert(0, _SD)
import memory_env
import sqlite3


def _db():
    return sqlite3.connect(memory_env.store_db())


def _parse_entry(text):
    """Split a stored entry into its [claim]/[verdict]/[evidence] parts."""
    claim = re.search(r"\[claim\]\s*(.+)", text)
    verdict = re.search(r"\[verdict\]\s*(.+)", text)
    evidence = re.search(r"\[evidence\]\s*(.+)", text)
    return {
        "claim": claim.group(1).strip() if claim else "",
        "verdict": verdict.group(1).strip() if verdict else "",
        "evidence": evidence.group(1).strip() if evidence else "",
    }


def export(out_dir=None, wings=None, limit=None):
    out_dir = out_dir or os.path.join(os.path.expanduser("~"),
                                      "ai", "ft-rag-data")
    os.makedirs(out_dir, exist_ok=True)
    db = _db()
    db.row_factory = sqlite3.Row
    # TAXONOMY-INTEGRATED: pull from the taxonomy wings dynamically rather than
    # a hardcoded list. If no wings given, auto-discover the non-transcript,
    # non-archival wings from the store (the absorbed-understanding wings).
    if not wings:
        try:
            # Prefer ABSORBED-UNDERSTANDING wings: those whose entries carry the
            # [claim]/[verdict] structure (the research-verify-absorb output).
            # Bulk archive wings (sagan-books, transcripts) have thousands of
            # chunks but are NOT Q/A pairs — they'd swamp the dataset with raw
            # text. So: scan candidate wings, keep only those with parseable
            # [claim]+[verdict] entries, and rank by how many parseable pairs.
            cand = db.execute(
                "SELECT wing FROM chunks GROUP BY wing").fetchall()
            scored = []
            for r in cand:
                w = r["wing"]
                if w in ("transcripts", "gutenberg", "general", "mempalace"):
                    continue
                sample = db.execute(
                    "SELECT text FROM chunks WHERE wing=? LIMIT 6", (w,)).fetchall()
                pairs = sum(1 for s in sample
                            if _parse_entry(s["text"])["claim"]
                            and _parse_entry(s["text"])["verdict"])
                if pairs:
                    scored.append((pairs, w))
            scored.sort(reverse=True)
            wings = [w for _, w in scored][:6] or ["politics"]
        except Exception:
            wings = ["politics"]
    # 1) Absorbed wings -> Q/A pairs (claim is the question; verdict+evidence the answer).
    pairs = []
    for wing in wings:
        rows = db.execute(
            "SELECT text FROM chunks WHERE wing=? ORDER BY ingested_at DESC",
            (wing,)).fetchall()
        for r in rows:
            p = _parse_entry(r["text"])
            if not p["claim"] or not p["verdict"]:
                continue
            answer = f"{p['verdict']}\n{p['evidence']}".strip()
            if len(answer) < 20:
                continue
            pairs.append({
                "instruction": f"Assess this claim: {p['claim']}",
                "output": answer,
                "source": wing,
            })
    # 2) Constitution -> behavioral instruction pairs.
    crow = db.execute(
        "SELECT text FROM entries WHERE topic='constitution' AND always_on=1").fetchall()
    for r in crow:
        t = (r["text"] or "").strip()
        if len(t) < 20:
            continue
        pairs.append({
            "instruction": "What is an inviolable rule of this system?",
            "output": t,
            "source": "constitution",
        })
    if limit:
        pairs = pairs[:limit]
    # Write dataset (both the raw and a sharegpt-style format).
    raw_path = os.path.join(out_dir, "ft_rag_dataset.jsonl")
    with open(raw_path, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    # Verdict distribution summary.
    dist = {}
    for p in pairs:
        if p["source"] == "constitution":
            dist["constitution"] = dist.get("constitution", 0) + 1
        else:
            v = p["output"].split()[0].upper()
            dist[v] = dist.get(v, 0) + 1
    tokens = sum(len(p["output"].split()) for p in pairs)
    report = {
        "pairs": len(pairs),
        "verdict_distribution": dist,
        "approx_tokens": tokens,
        "dataset": raw_path,
        "sources": ["politics wing (verified, verdict-tagged)", "constitution (always-on)"],
        "note": "Fine-tune a small local model (Bolmo-1B or similar) on this dataset, "
                "then keep RAG active — the research shows FT+RAG beats either alone.",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rep_path = os.path.join(out_dir, "report.json")
    with open(rep_path, "w") as f:
        json.dump(report, f, indent=2)
    db.close()
    return report


def report(out_dir=None):
    out_dir = out_dir or os.path.join(os.path.expanduser("~"),
                                      "ai", "ft-rag-data")
    p = os.path.join(out_dir, "report.json")
    if not os.path.exists(p):
        return {"error": "no export yet; run export first"}
    with open(p) as f:
        return json.load(f)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="FT+RAG training-data exporter")
    ap.add_argument("cmd", choices=["export", "report"])
    ap.add_argument("--wing", nargs="*", help="wings to export (default: auto-discover)")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.cmd == "export":
        r = export(args.out, args.wing, args.limit)
        print(json.dumps(r, indent=2))
    else:
        print(json.dumps(report(args.out), indent=2))
