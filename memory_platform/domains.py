#!/usr/bin/env python3
"""domains.py — portable domain taxonomy with store-derived sub-domains.

The worthiness filter and research lens both need to know "what the user works
on". This module provides the portable answer:

  TOP_LEVEL_DOMAINS  — a small, generic set of domains (software, teaching,
                       writing, media, research, health, business, personal
                       craft) with detection keywords. These are the SAME for
                       every user — they are code, not personal data.

  discover_sub_domains() — reads the hybrid store's actual topic coverage and
                       returns the USER'S SPECIFIC TOPICS as sub-domains under
                       the top-level domains they belong to. The user's
                       domains become DATA (store topics), not code. On any
                       new machine, the store starts empty and the sub-domains
                       grow as the user works.

Example: a user who works on skill-based teaching and media production gets:
    teaching  -> [foundational pedagogy, beginner curriculum]
    media     -> [production workflow, recording basics]

The same code serves any user; only the store contents differ.
"""

import json
import os

import memory_env

# Top-level portable domains: keyword families that detect whether an incoming
# fact/query belongs to a domain. These are generic by design — the SAME for
# every user, never personal data.
TOP_LEVEL_DOMAINS = {
    "software": ["code", "script", "plugin", "software", "program", "api",
                 "database", "automation", "workflow", "tooling", "deploy",
                 "testing", "debug", "cli", "framework", "repository"],
    "teaching": ["teach", "course", "lesson", "learner", "pedagog", "curriculum",
                 "tutor", "education", "training", "syllabus", "exercise"],
    "writing": ["write", "writing", "article", "blog", "copy", "document",
                "editorial", "draft", "prose", "publish"],
    "media": ["video", "audio", "recording", "production", "edit", "daw",
              "podcast", "music", "sound", "mix", "master"],
    "research": ["research", "study", "analysis", "experiment", "evidence",
                 "falsif", "method", "literature", "replicat", "evaluate"],
    "health": ["health", "fitness", "nutrition", "diet", "wellbeing", "exercise",
               "sleep", "medical", "training plan"],
    "business": ["business", "marketing", "sales", "client", "customer",
                 "finance", "pricing", "brand", "campaign", "strategy"],
    "personal-craft": ["craft", "hobby", "collect", "build", "make", "workshop",
                       "studio", "gear", "hands-on"],
    "communication": ["communicat", "respond", "explain", "present", "convince",
                      "direct", "honest", "source", "evidence", "teach",
                      "assist", "guide", "recall", "organise", "plan",
                      "question", "answer", "help"],
}

# Sub-domain anchors: a known specific topic maps its parent domain. This is a
# tiny seed list of generic sub-domain names (not user data); the store's own
# topics are the primary source of sub-domains.
SEED_SUB_DOMAINS = {
    "software": ["developer tools", "automation"],
    "teaching": ["beginner course", "learning design"],
    "media": ["audio production", "video editing"],
    "research": ["evidence evaluation", "critical thinking"],
    "communication": ["clear delivery", "evidence-based answers"],
}


def _store_topics(db_path=None):
    """Read the store's distinct topic tags (the user's actual coverage)."""
    db_path = db_path or memory_env.store_db()
    if not os.path.exists(db_path):
        return []
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT DISTINCT topic FROM entries "
            "WHERE status='active' AND topic NOT IN "
            "('constitution','identity','safety','operating','human','project',"
            "'warm','general','transcripts')").fetchall()
        db.close()
        return [r[0] for r in rows if r[0] and r[0].strip()]
    except Exception:
        return []


def domain_of(text):
    """Which top-level domain(s) does `text` belong to? Returns matched domain
    names (list)."""
    low = (text or "").lower()
    hits = []
    for domain, kws in TOP_LEVEL_DOMAINS.items():
        if any(k in low for k in kws):
            hits.append(domain)
    return hits


def discover_sub_domains(db_path=None):
    """Return the user's actual sub-domains grouped under their top-level
    domain: {domain: [sub_domain, ...]}. Sub-domains come from (1) the store's
    real topic tags and (2) a small generic seed list. Empty on a fresh
    machine — grows as the user works."""
    out = {d: [] for d in TOP_LEVEL_DOMAINS}
    for topic in _store_topics(db_path):
        if not topic:
            continue
        parent = domain_of(topic)
        for p in parent:
            if topic not in out.get(p, []):
                out.setdefault(p, []).append(topic)
    for d, subs in SEED_SUB_DOMAINS.items():
        for s in subs:
            if s not in out.get(d, []):
                out.setdefault(d, []).append(s)
    return {d: subs for d, subs in out.items() if subs}


def coverage_report(db_path=None):
    """Human-readable coverage: which domains the user works in."""
    subs = discover_sub_domains(db_path)
    if not subs:
        return "no domains covered yet (store empty)"
    return "; ".join(f"{d}: {', '.join(sub)}" for d, sub in sorted(subs.items()))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["domains", "sub", "coverage"])
    ap.add_argument("text", nargs="*", default=[])
    args = ap.parse_args()
    if args.cmd == "domains":
        print(json.dumps(TOP_LEVEL_DOMAINS, indent=2))
    elif args.cmd == "sub":
        print(json.dumps(discover_sub_domains(), indent=2))
    else:
        print(coverage_report())
