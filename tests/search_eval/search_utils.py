from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCORE_MIN = 0
SCORE_MAX = 4
USEFUL_SCORE_THRESHOLD = 3

QUERY_SCORE_WEIGHT_TOP_1 = 40
QUERY_SCORE_WEIGHT_SUCCESS_AT_3 = 35
QUERY_SCORE_WEIGHT_BEST_TOP_5 = 25


def normalize_search_results(raw_results: Any, top_n: int) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for item in raw_results or []:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "snippet": str(item.get("snippet", item.get("content", ""))).strip(),
            }
        )
    return normalized[:top_n]


def format_results_for_grader(results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for index, result in enumerate(results, 1):
        title = (result.get("title") or "no title").replace("\n", " ")
        snippet = (result.get("snippet") or "no snippet").replace("\n", " ")
        url = (result.get("url") or "no url").replace("\n", " ")
        lines.append(f"[{index}] Title: {title}")
        lines.append(f"    URL: {url}")
        lines.append(f"    Snippet: {snippet[:280]}")
        lines.append("")
    return "\n".join(lines)


def extract_json_payload(text: str) -> Dict[str, Any] | None:
    fenced_block_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    candidates = [fenced_block_match.group(1)] if fenced_block_match else []
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        start = candidate.find("{")
        if start == -1:
            continue
        depth = 0
        for end in range(start, len(candidate)):
            if candidate[end] == "{":
                depth += 1
            elif candidate[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : end + 1])
                    except json.JSONDecodeError:
                        continue
        try:
            return json.loads(candidate[start:])
        except json.JSONDecodeError:
            continue

    return None


def normalize_grader_scores(raw_scores: Any, target_count: int) -> List[int]:
    scores: List[int] = []
    if isinstance(raw_scores, list):
        for score in raw_scores:
            try:
                score_value = int(score)
            except (TypeError, ValueError):
                continue
            score_value = max(SCORE_MIN, min(SCORE_MAX, score_value))
            scores.append(score_value)

    if len(scores) < target_count:
        scores.extend([0] * (target_count - len(scores)))
    if len(scores) > target_count:
        return scores[:target_count]
    return scores


def build_failure_reasons(
    category: str,
    target_count: int,
    top_result_score: int,
    top_3_scores: List[int],
    success_at_5: bool,
) -> List[str]:
    failure_reasons: List[str] = []
    if target_count == 0:
        failure_reasons.append("no_results")
    if not success_at_5:
        failure_reasons.append("no_useful_result_top_5")
    if top_3_scores and all(score <= 1 for score in top_3_scores):
        failure_reasons.append("top_3_all_weak_or_irrelevant")
    if top_result_score == 0:
        failure_reasons.append("top_result_irrelevant")
    if (category or "").strip().lower() == "exact lookup" and not success_at_5:
        failure_reasons.append("exact_lookup_without_useful_result")
    return failure_reasons


def empty_metrics() -> Dict[str, Any]:
    return {
        "average_relevance": 0.0,
        "top1_relevance": 0.0,
        "top3_relevance": 0.0,
        "average_query_score": 0.0,
        "success_at_1": 0.0,
        "success_at_3": 0.0,
        "success_at_5": 0.0,
        "catastrophic_failure_rate": 0.0,
        "per_category": {},
        "worst_queries": [],
    }


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    raw_value = raw_value.strip().lower()
    return raw_value in ("1", "true", "yes")


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, data: Any, indent: int | None = 2) -> None:
    path.write_text(
        json.dumps(data, indent=indent, ensure_ascii=False),
        encoding="utf-8",
    )


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def read_jsonl_file(
    path: Path,
    on_decode_error: Optional[Callable[[json.JSONDecodeError], None]] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            if on_decode_error is not None:
                on_decode_error(exc)
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def append_jsonl_record(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
