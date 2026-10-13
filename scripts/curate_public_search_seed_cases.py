#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from datasets import load_dataset

from run_odysseus_search_teacher_pipeline import call_deepseek_json, db_deepseek_endpoint


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/evals/ody_public_search_seed_20260825/cases.json"
DEFAULT_LOCAL_SEEDS: list[Path] = []


QUESTION_RE = re.compile(r"\?$|^(?:who|what|when|where|why|how|which|can|does|do|is|are|was|were)\b", re.I)
PRIVATE_RE = re.compile(
    r"\b(my|our)\s+(?:email|inbox|calendar|notes?|documents?|files?|computer|desktop|downloads?|contacts?)\b|"
    r"\b(?:send|delete|archive|mark|reply to|draft|schedule|remind me|open my)\b",
    re.I,
)
TOO_CURRENT_RE = re.compile(r"\b(?:today|right now|current|latest|this week|this month|2026|2025)\b", re.I)


def stable_id(prefix: str, value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True)
    return f"{prefix}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def useful_question(text: str) -> bool:
    q = clean_text(text)
    if len(q) < 18 or len(q) > 240:
        return False
    if PRIVATE_RE.search(q):
        return False
    if not QUESTION_RE.search(q):
        return False
    if len(q.split()) < 5:
        return False
    return True


def prompt_variant(question: str, source: str, index: int) -> str:
    q = clean_text(question).rstrip("?")
    variants = [
        f"Search the web and answer this: {q}?",
        f"Can you look up {q} and give me the answer?",
        f"Find a reliable source for this and answer briefly: {q}?",
        f"Use search to verify: {q}?",
        f"I need a quick sourced answer: {q}?",
    ]
    if source == "hotpot_qa":
        variants.extend([
            f"Search for the two facts needed to answer this: {q}?",
            f"Look this up and combine the evidence: {q}?",
        ])
    return variants[index % len(variants)]


def add_candidate(out: list[dict[str, Any]], seen: set[str], *, source: str, question: str, answer: Any = "", family: str = "") -> None:
    question = clean_text(question)
    if not useful_question(question):
        return
    key = question.lower()
    if key in seen:
        return
    seen.add(key)
    idx = len(out)
    out.append({
        "source": source,
        "source_id": stable_id(source, question),
        "question": question,
        "answer_hint": clean_text(answer)[:220],
        "family": family or ("fresh_or_date_sensitive" if TOO_CURRENT_RE.search(question) else "public_fact_search"),
        "user": prompt_variant(question, source, idx),
    })


def sample_nq_open(out: list[dict[str, Any]], seen: set[str], target: int, seed: int) -> None:
    ds = load_dataset("nq_open", split="train", streaming=True)
    rng = random.Random(seed)
    for i, row in enumerate(ds):
        if i > 250_000 or len(out) >= target:
            break
        if rng.random() > 0.045:
            continue
        add_candidate(
            out,
            seen,
            source="nq_open",
            question=row.get("question"),
            answer=row.get("answer"),
            family="simple_public_fact",
        )


def sample_hotpot(out: list[dict[str, Any]], seen: set[str], target: int, seed: int) -> None:
    ds = load_dataset("hotpot_qa", "distractor", split="train", streaming=True)
    rng = random.Random(seed + 17)
    for i, row in enumerate(ds):
        if i > 180_000 or len(out) >= target:
            break
        if rng.random() > 0.075:
            continue
        add_candidate(
            out,
            seen,
            source="hotpot_qa",
            question=row.get("question"),
            answer=row.get("answer"),
            family=f"multi_hop_{clean_text(row.get('type') or 'qa')}",
        )


def load_local(out: list[dict[str, Any]], seen: set[str], paths: list[Path], target: int) -> None:
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if len(out) >= target:
                return
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = clean_text(row.get("prompt") or row.get("user") or row.get("question"))
            if not prompt or PRIVATE_RE.search(prompt) or len(prompt) > 1600:
                continue
            key = prompt.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source": f"local:{path.name}",
                "source_id": clean_text(row.get("task_id") or row.get("id") or stable_id(path.name, prompt)),
                "question": prompt,
                "answer_hint": clean_text(row.get("reference_solution") or row.get("answer"))[:500],
                "family": clean_text(row.get("task_family") or row.get("family") or "local_web_research"),
                "user": prompt,
            })


def heuristic_rank(item: dict[str, Any]) -> float:
    q = item["question"].lower()
    score = 0.0
    score += 1.0 if item["source"] == "nq_open" else 0.0
    score += 1.4 if item["source"] == "hotpot_qa" else 0.0
    score += 1.0 if item["source"].startswith("local:") else 0.0
    score += 0.4 if 7 <= len(q.split()) <= 22 else 0.0
    score += 0.5 if re.search(r"\b(which|compare|both|between|relationship|part of|head office)\b", q) else 0.0
    score += 0.3 if item.get("answer_hint") else 0.0
    score -= 0.7 if TOO_CURRENT_RE.search(q) else 0.0
    score -= 0.8 if re.search(r"\b(song|lyrics|movie cast|episode)\b", q) else 0.0
    return score


def deepseek_audit(endpoint: dict[str, str], items: list[dict[str, Any]], batch_size: int) -> dict[str, dict[str, Any]]:
    audits: dict[str, dict[str, Any]] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        payload = {
            "task": "Audit public web-search SFT seed prompts. Pick prompts that are natural, generic, useful for teaching a web_search/web_fetch agent, and not private/user-data tasks.",
            "current_date": "2026-08-25",
            "rating_scale": "0 reject, 1 weak, 2 usable, 3 good, 4 excellent",
            "reject_if": [
                "requires private data, email, calendar, local files, account access, login, or sending/deleting actions",
                "too broad for a 1-3 web tool trace unless it is a small minority of deep research seeds",
                "answer is purely subjective or does not benefit from search",
                "current/date-sensitive but lacks a stable phrasing or source date expectation",
                "unsafe medical/legal/financial advice beyond general sourced information",
            ],
            "items": [
                {
                    "id": item["source_id"],
                    "source": item["source"],
                    "family": item["family"],
                    "user": item["user"],
                    "answer_hint": item.get("answer_hint") or "",
                }
                for item in batch
            ],
            "return_schema": {
                "audits": [
                    {"id": "string", "rating": 0, "keep": False, "family": "string", "reason": "string"}
                ]
            },
        }
        result = call_deepseek_json(endpoint, payload, max_tokens=5000, temperature=0.15, json_mode=True)
        for audit in result.get("audits") or []:
            if not isinstance(audit, dict):
                continue
            item_id = clean_text(audit.get("id"))
            if item_id:
                audits[item_id] = audit
        print(json.dumps({"stage": "deepseek_audit", "start": start, "batch": len(batch), "audited": len(audits)}), flush=True)
    return audits


def build_cases(items: list[dict[str, Any]], audits: dict[str, dict[str, Any]], count: int) -> list[dict[str, Any]]:
    ranked: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for item in items:
        audit = audits.get(item["source_id"]) or {}
        rating = float(audit.get("rating") or 0)
        if audit and not audit.get("keep"):
            continue
        if rating < 2:
            continue
        ranked.append((rating * 10 + heuristic_rank(item), item, audit))
    ranked.sort(key=lambda x: x[0], reverse=True)
    cases = []
    family_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for _score, item, audit in ranked:
        family = clean_text(audit.get("family") or item.get("family") or "web")
        source = item["source"]
        if family_counts.get(family, 0) >= max(40, count // 5):
            continue
        if source_counts.get(source, 0) >= max(80, int(count * 0.55)):
            continue
        cases.append({
            "id": f"public_search_seed_{len(cases):04d}",
            "kind": "web",
            "family": family,
            "source_dataset": source,
            "source_id": item["source_id"],
            "user": item["user"],
            "expect_first_tool": "web_search",
            "allow_web_search": True,
            "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links", "Web sources", "from the search results", "snippets"],
            "why_search_needed": clean_text(audit.get("reason") or "public source-backed answer"),
            "answer_hint": item.get("answer_hint") or "",
        })
        family_counts[family] = family_counts.get(family, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(cases) >= count:
            break
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--candidate-count", type=int, default=900)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--audit-batch-size", type=int, default=35)
    parser.add_argument("--skip-deepseek", action="store_true")
    parser.add_argument("--local-seed", action="append", type=Path, default=[])
    args = parser.parse_args()

    rng = random.Random(args.seed)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    local_paths = args.local_seed or DEFAULT_LOCAL_SEEDS
    load_local(candidates, seen, local_paths, min(args.candidate_count, 120))
    sample_hotpot(candidates, seen, max(args.candidate_count // 2, 260), args.seed)
    sample_nq_open(candidates, seen, args.candidate_count, args.seed)
    rng.shuffle(candidates)
    candidates.sort(key=heuristic_rank, reverse=True)
    candidates = candidates[: args.candidate_count]

    endpoint = db_deepseek_endpoint()
    endpoint["model"] = args.__dict__.get("teacher_model") or endpoint.get("model") or "deepseek-chat"
    if args.skip_deepseek:
        audits = {
            item["source_id"]: {
                "id": item["source_id"],
                "rating": 3,
                "keep": True,
                "family": item["family"],
                "reason": "heuristic keep",
            }
            for item in candidates
        }
    else:
        audits = deepseek_audit(endpoint, candidates, args.audit_batch_size)

    cases = build_cases(candidates, audits, args.count)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": Path(__file__).name,
        "current_date": "2026-08-25",
        "source_notes": [
            "nq_open / Natural Questions: CC-BY-SA-3.0 on Hugging Face.",
            "hotpot_qa: CC-BY-SA-4.0 on Hugging Face.",
            "local research seeds are prompt seeds only; inspect before training if exporting outside this workspace.",
        ],
        "candidate_count": len(candidates),
        "audit_count": len(audits),
        "cases": cases,
        "audit_summary": {
            "accepted_cases": len(cases),
            "sources": {source: sum(1 for c in cases if c.get("source_dataset") == source) for source in sorted({c.get("source_dataset") for c in cases})},
            "families": {family: sum(1 for c in cases if c.get("family") == family) for family in sorted({c.get("family") for c in cases})},
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out.parent / "seed_audits.json").write_text(json.dumps({"audits": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out.parent / "seed_candidates.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates),
        encoding="utf-8",
    )
    print(json.dumps({"cases": len(cases), "candidates": len(candidates), "out": str(args.out)}, indent=2))
    if len(cases) < args.count:
        raise RuntimeError(f"Only built {len(cases)} cases; requested {args.count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
