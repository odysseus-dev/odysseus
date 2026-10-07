#!/usr/bin/env python3
"""Cook every historical SFT Alex user turn into a fresh tool conversation."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import re
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from odysseus_conversation_qa import (
    DEFAULT_DATA,
    DEFAULT_JUDGE_ENDPOINT,
    DEFAULT_JUDGE_MODEL,
    FAMILY_SEEDS,
    compact_tool_catalog,
    endpoint_from_db,
    teacher_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEEDS = ROOT / "tmp/odysseus-conversation-qa/sft-alex-all-seeds.json"
DEFAULT_OUTPUT = ROOT / "tmp/odysseus-conversation-qa/sft-alex-cooked.jsonl"
LOCK = threading.Lock()

_CREATE_RE = re.compile(
    r"\b(?:create|make|start|write|add|save|draft|new)\b", re.IGNORECASE
)
_LOOKUP_RE = re.compile(
    r"\b(?:open|find|show|read|list|search|retrieve|look\s+up|already\s+have|saved)\b",
    re.IGNORECASE,
)
_NEW_TOPIC_RE = re.compile(
    r"\b(?:about|on)\s+(.+?)(?=\s+(?:and|then|with|using)\b|[.!?]|$)",
    re.IGNORECASE,
)
_ENTITY_PATTERNS = (
    re.compile(r"([`\"])([^`\"\r\n]{3,120})\1"),
    re.compile(r"https?://[^\s<>]+", re.IGNORECASE),
    re.compile(r"\b[\w.-]+\.(?:md|txt|csv|json|pdf|html|docx?|xlsx?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:titled|called|named)\s+(.+?)(?=\s+(?:with|in|so|and|for|from|that)\b|[.!?,;]|$)",
        re.IGNORECASE,
    ),
)


def explicit_entities(text: str) -> set[str]:
    """Extract source-grounded names that a cooked flow must not replace."""
    entities: set[str] = set()
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            if pattern is _ENTITY_PATTERNS[0]:
                value = match.group(2).strip()
            else:
                value = (match.group(1) if match.lastindex else match.group(0)).strip()
            if len(value) >= 3:
                entities.add(value.casefold())
    return entities


def grounding_issues(seed: dict[str, Any], flow: dict[str, Any]) -> list[str]:
    """Reject synthetic flows whose private-object state contradicts the seed."""
    source_turns = [str(item.get("user") or "") for item in seed.get("context") or []]
    generated_turns = [str(item.get("user") or "") for item in flow.get("turns") or []]
    source_text = "\n".join(source_turns)
    generated_text = "\n".join(generated_turns)
    issues: list[str] = []

    for entity in sorted(explicit_entities(source_text)):
        if entity not in generated_text.casefold():
            issues.append(f"missing_source_entity:{entity}")

    # Standalone flows must recreate source-created private state before use.
    for index, source_turn in enumerate(source_turns[:-1]):
        if not _CREATE_RE.search(source_turn):
            continue
        entities = explicit_entities(source_turn)
        later_source = "\n".join(source_turns[index + 1:]).casefold()
        for entity in entities:
            if entity not in later_source:
                continue
            mentions = [turn for turn in generated_turns if entity in turn.casefold()]
            if mentions and not _CREATE_RE.search(mentions[0]):
                issues.append(f"unestablished_private_entity:{entity}")

    # A source topic introduced by create/start cannot become pre-existing state.
    target = str(seed.get("target_user") or "")
    if _CREATE_RE.search(target):
        target_entities = explicit_entities(target)
        target_entities.update(
            match.group(1).strip().casefold()
            for match in _NEW_TOPIC_RE.finditer(target)
            if len(match.group(1).strip()) >= 3
        )
        for entity in target_entities:
            for turn in generated_turns:
                if entity not in turn.casefold():
                    continue
                if _CREATE_RE.search(turn):
                    break
                if _LOOKUP_RE.search(turn):
                    issues.append(f"lookup_before_creation:{entity}")
                    break
    return sorted(set(issues))


def redact(text: str) -> str:
    """Remove likely credentials while retaining natural request structure."""
    value = str(text or "")
    value = re.sub(r"hf_[A-Za-z0-9]{20,}", "[REDACTED_HF_TOKEN]", value)
    value = re.sub(r"(?i)(api[_ -]?key|token|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]", value)
    return value[:1200]


def load_seeds(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    seeds = payload.get("seeds") if isinstance(payload, dict) else None
    if not isinstance(seeds, list):
        raise RuntimeError("seed file must contain a top-level seeds array")
    output = []
    for seed in seeds:
        if not isinstance(seed, dict) or not seed.get("seed_id"):
            continue
        row = dict(seed)
        row["context"] = [
            {"user": redact(item.get("user", ""))}
            for item in (seed.get("context") or []) if isinstance(item, dict)
        ]
        row["target_user"] = redact(seed.get("target_user", ""))
        output.append(row)
    return output


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("source_seed_id"):
            ids.add(str(row["source_seed_id"]))
    return ids


def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def validate_flows(
    result: Any,
    wanted: set[str],
    seeds: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    rows = result.get("flows") if isinstance(result, dict) else None
    valid: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return valid
    for row in rows:
        if not isinstance(row, dict):
            continue
        seed_id = str(row.get("source_seed_id") or "")
        turns = row.get("turns")
        if seed_id not in wanted or seed_id in valid:
            continue
        if row.get("family") not in FAMILY_SEEDS or not isinstance(turns, list) or not 2 <= len(turns) <= 4:
            continue
        if any(not isinstance(turn, dict) or not str(turn.get("user") or "").strip() for turn in turns):
            continue
        if seeds and seed_id in seeds and grounding_issues(seeds[seed_id], row):
            continue
        row["id"] = "sft-alex-" + re.sub(r"[^A-Za-z0-9_-]", "-", seed_id)[:72]
        row["source_seed_id"] = seed_id
        valid[seed_id] = row
    return valid


def cook_batch(endpoint: Any, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = {str(seed["seed_id"]): seed for seed in batch}
    cooked: dict[str, dict[str, Any]] = {}
    for _ in range(3):
        if not pending:
            break
        result = teacher_json(endpoint, {
            "task": "Turn every supplied historical seed into one fresh realistic multi-turn conversation that tests Odysseus tool use.",
            "rules": [
                "Return exactly one flow for every source_seed_id; never merge, omit, or duplicate seeds.",
                "Preserve the seed's behavioral intent, but do not copy its wording mechanically.",
                "Preserve exact names, titles, filenames, URLs, contacts, and named research topics from the source seed; never replace them with invented private objects.",
                "Every generated flow is replayed independently against a clean fixture. If a later action depends on an object created earlier in the source context, include that creation before using the object.",
                "Never find, open, or read an invented private object. A new note, document, task, event, skill, email, or research report must be created earlier in that generated flow.",
                "Each flow has 2-4 user turns and at least one context-dependent follow-up.",
                "The conversation must naturally require at least one Odysseus tool; for a general question, add an adjacent save, verify, open, or retrieve request.",
                "Use natural short wording and occasional realistic misspelling, not regex-like substitutions.",
                "Do not include record IDs, credentials, real email addresses, destructive shell operations, email sending, purchases, or irreversible actions.",
                "Expected behavior is semantic and names the appropriate action/tool family without prescribing exact prose.",
                "Choose exactly one canonical family from the supplied family list; use switching when the conversation crosses families.",
            ],
            "schema": {"flows": [{
                "source_seed_id": "exact supplied ID", "id": "short ID",
                "family": "canonical family", "purpose": "behavior under test",
                "turns": [{"user": "message", "expect": "semantic expected behavior"}],
            }]},
            "canonical_families": sorted(FAMILY_SEEDS),
            "complete_odysseus_tool_catalog": compact_tool_catalog(),
            "seeds": list(pending.values()),
        }, max_tokens=7500, temperature=0.65)
        accepted = validate_flows(result, set(pending), pending)
        cooked.update(accepted)
        for seed_id in accepted:
            pending.pop(seed_id, None)
    if pending:
        raise RuntimeError(f"teacher omitted {len(pending)} seeds: {sorted(pending)[:3]}")
    return [cooked[str(seed["seed_id"])] for seed in batch]


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--endpoint-id", default=DEFAULT_JUDGE_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = args.output.with_suffix(args.output.suffix + ".lock")
    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another cooker already owns {lock_path}")
    endpoint = endpoint_from_db(args.data_dir, args.endpoint_id, args.model)
    seeds = load_seeds(args.seeds)
    done = completed_ids(args.output)
    pending = [seed for seed in seeds if str(seed["seed_id"]) not in done]
    if args.limit is not None:
        pending = pending[:args.limit]
    batches = chunks(pending, args.batch_size)
    failures: list[str] = []
    cooked_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {pool.submit(cook_batch, endpoint, batch): batch for batch in batches}
        for future in concurrent.futures.as_completed(future_map):
            batch = future_map[future]
            try:
                rows = future.result()
                append_rows(args.output, rows)
                cooked_count += len(rows)
                print(json.dumps({"cooked": len(done) + cooked_count, "total": len(seeds)}), flush=True)
            except Exception as exc:
                failures.extend(str(seed["seed_id"]) for seed in batch)
                print(json.dumps({"batch_failed": len(batch), "error": repr(exc)}), flush=True)
    counts = Counter()
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            try:
                counts[json.loads(line).get("family", "unknown")] += 1
            except (json.JSONDecodeError, AttributeError):
                pass
    print(json.dumps({
        "source_seeds": len(seeds), "already_done": len(done),
        "cooked_now": cooked_count, "failed": len(failures),
        "remaining": len(seeds) - len(done) - cooked_count,
        "families": dict(sorted(counts.items())),
    }, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
