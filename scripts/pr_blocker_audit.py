#!/usr/bin/env python3
"""Read-only pull request overlap audit helper.

This script intentionally does not import the Odysseus application package.
It only reads local JSON input or invokes read-only `gh` list/API commands.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


AREA_RULES = [
    (
        "Auth / users / API tokens",
        ("auth", "token", "api_key", "api-key", "apikey", "login", "totp"),
        ("auth", "bearer token", "api token", "api key", "login", "privilege", "permission"),
    ),
    (
        "Memory / RAG / vector store",
        ("memory", "rag", "vector", "embedding", "faiss", "chroma"),
        ("memory", "rag", "vector", "embedding", "retrieval"),
    ),
    ("Search / web search", ("search", "ddg", "web_search"), ("search", "ddg", "web")),
    (
        "Model routing / endpoint discovery",
        ("model", "llm", "endpoint", "lmstudio", "ollama"),
        ("model", "routing", "endpoint", "discovery", "llm"),
    ),
    (
        "Agent loop / tools",
        ("agent", "tool", "function_call", "mcp", "shell"),
        ("agent", "tool", "function", "mcp"),
    ),
    ("Cookbook / runners", ("cookbook", "runner", "preset"), ("cookbook", "runner", "preset")),
    ("Email / CalDAV", ("mail", "email", "imap", "caldav", "calendar"), ("email", "mail", "caldav", "calendar")),
    (
        "Documents / uploads",
        ("document", "upload", "attachment", "processor", "markitdown"),
        ("document", "upload", "attachment"),
    ),
    ("Gallery / visual report", ("gallery", "image", "vision", "preview"), ("gallery", "visual", "image")),
    (
        "CI / repo process",
        (".github", "docker", "compose", "workflow", "ci", "pytest"),
        ("ci", "workflow", "docker", "compose"),
    ),
    (
        "Docs / tooling / tests",
        ("docs/", "scripts/", "tests/", "README", "tooling"),
        ("docs", "test", "tooling", "script"),
    ),
]

ALL_AREAS = [rule[0] for rule in AREA_RULES] + ["Other"]
WORD_RE = re.compile(r"[a-z0-9]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
ANSI = {
    "bold": "\033[1m",
    "bold_red": "\033[1;31m",
    "bold_cyan": "\033[1;36m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}
STOP_WORDS = {
    "a",
    "add",
    "and",
    "bug",
    "fix",
    "for",
    "in",
    "new",
    "of",
    "pr",
    "the",
    "to",
    "update",
}


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str
    url: str
    files: tuple[str, ...]
    merge_state: str
    review_decision: str
    updated_at: str
    areas: tuple[str, ...]


@dataclass(frozen=True)
class ScoredPullRequest:
    pr: PullRequest
    score: int
    reasons: tuple[str, ...]


class ProgressReporter:
    def __init__(self, enabled: bool, stream=None):
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.last_len = 0

    def phase(self, message: str) -> None:
        if self.enabled:
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def update(self, done: int, total: int, files_count: int, missing_count: int, number: int) -> None:
        if not self.enabled:
            return
        percent = int(done * 100 / total) if total else 100
        line = (
            f"Fetching changed files: {done}/{total} PRs ({percent}%) | "
            f"files {files_count} | missing {missing_count} | #{number}"
        )
        line = line[:140]
        padding = max(self.last_len - len(line), 0)
        self.stream.write(f"\r{line}{' ' * padding}")
        self.stream.flush()
        self.last_len = len(line)

    def finish_line(self) -> None:
        if self.enabled and self.last_len:
            self.stream.write(f"\r{' ' * self.last_len}\r")
            self.stream.flush()
            self.last_len = 0

    def summary(self, message: str) -> None:
        if self.enabled:
            self.finish_line()
            self.stream.write(f"{message}\n")
            self.stream.flush()


def load_json_file(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc


def fetch_live_prs(repo: str, fetch_files: bool = True, progress: ProgressReporter | None = None, limit: int = 1000):
    progress = progress or ProgressReporter(False)
    fields = (
        "number,title,author,files,mergeStateStatus,reviewDecision,updatedAt,url"
        if fetch_files
        else "number,title,author,mergeStateStatus,reviewDecision,updatedAt,url"
    )
    cmd = ["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", str(limit), "--json", fields]
    progress.phase("Fetching open PR list...")
    try:
        payload = _run_gh_json(cmd)
    except RuntimeError:
        api_path = f"repos/{repo}/pulls?state=open&per_page=100"
        payload = _run_gh_json(["gh", "api", "--paginate", api_path])
        payload = _limit_payload(payload, limit)
    if not fetch_files:
        return payload
    return _fill_missing_live_files(repo, payload, progress)


def _limit_payload(payload, limit: int):
    if isinstance(payload, dict):
        raw_prs = payload.get("items", [])
        if isinstance(raw_prs, list):
            return {**payload, "items": raw_prs[:limit]}
        return payload
    if isinstance(payload, list):
        return payload[:limit]
    return payload


def _fill_missing_live_files(repo: str, payload, progress: ProgressReporter | None = None):
    progress = progress or ProgressReporter(False)
    raw_prs = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_prs, list):
        return payload

    warnings = []
    targets = [item for item in raw_prs if isinstance(item, dict)]
    progress.phase(f"Fetching changed files for {len(targets)} PRs...")
    fetched_count = 0
    files_count = 0
    missing_count = 0
    for done, item in enumerate(targets, start=1):
        number = _safe_int(item.get("number"))
        current_files = _extract_files(item.get("files", []))
        if not number:
            warnings.append("PR with missing number has no changed-file metadata")
            missing_count += 1
            progress.update(done, len(targets), files_count, missing_count, number)
            continue
        if current_files:
            fetched_count += 1
            files_count += len(current_files)
            progress.update(done, len(targets), files_count, missing_count, number)
            continue
        try:
            files = _fetch_live_pr_files(repo, number)
        except RuntimeError as exc:
            warnings.append(f"PR #{number}: could not fetch changed files: {exc}")
            missing_count += 1
            progress.update(done, len(targets), files_count, missing_count, number)
            continue
        item["files"] = [{"path": path} for path in files]
        files_count += len(files)
        if files:
            fetched_count += 1
        else:
            missing_count += 1
        progress.update(done, len(targets), files_count, missing_count, number)

    progress.summary(f"Fetched changed files for {fetched_count}/{len(targets)} PRs; {missing_count} missing metadata.")

    if isinstance(payload, dict):
        if warnings:
            payload["warnings"] = [*payload.get("warnings", []), *warnings]
        return payload
    if warnings:
        return {"items": payload, "warnings": warnings}
    return payload


def _fetch_live_pr_files(repo: str, number: int) -> list[str]:
    api_path = f"repos/{repo}/pulls/{number}/files?per_page=100"
    payload = _run_gh_json(["gh", "api", "--paginate", api_path])
    return _extract_files(payload)


def _run_gh_json(cmd: list[str]):
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{cmd[0]} exited with {result.returncode}")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh returned invalid JSON: {exc}") from exc


def normalize_prs(payload) -> list[PullRequest]:
    raw_prs = payload.get("items", []) if isinstance(payload, dict) else payload
    if raw_prs is None:
        raw_prs = []
    if not isinstance(raw_prs, list):
        raise ValueError("expected input JSON to be a list of pull requests or an object with an items list")
    return [normalize_pr(item) for item in raw_prs if isinstance(item, dict)]


def missing_file_metadata_count(prs: list[PullRequest]) -> int:
    return sum(1 for pr in prs if not pr.files)


def missing_metadata_warning(count: int) -> str:
    noun = "PR" if count == 1 else "PRs"
    return f"Warning: {count} {noun} still missing changed-file metadata."


def normalize_pr(item: dict) -> PullRequest:
    files = tuple(sorted(set(_extract_files(item.get("files", [])))))
    title = str(item.get("title") or "")
    areas = tuple(sorted(classify_areas(files, title)))
    return PullRequest(
        number=_safe_int(item.get("number")),
        title=title,
        author=_extract_author(item),
        url=str(item.get("url") or item.get("html_url") or ""),
        files=files,
        merge_state=str(item.get("mergeStateStatus") or item.get("merge_state_status") or item.get("mergeable_state") or "unknown"),
        review_decision=str(item.get("reviewDecision") or item.get("review_decision") or "unknown"),
        updated_at=str(item.get("updatedAt") or item.get("updated_at") or ""),
        areas=areas,
    )


def _extract_files(files) -> list[str]:
    if not isinstance(files, list):
        return []
    paths = []
    for entry in files:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict):
            path = entry.get("path") or entry.get("filename") or entry.get("name")
            if path:
                paths.append(str(path))
    return paths


def _extract_author(item: dict) -> str:
    author = item.get("author") or item.get("user") or {}
    if isinstance(author, dict):
        return str(author.get("login") or "unknown")
    return str(author or "unknown")


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def classify_areas(files: Iterable[str], title: str = "") -> set[str]:
    file_list = tuple(files)
    file_text = " ".join(file_list).lower()
    title_text = title.lower()
    areas = set()
    for area, path_keywords, title_keywords in AREA_RULES:
        if area == "Docs / tooling / tests":
            if is_docs_tooling_only(file_list) or title_strongly_indicates_docs_tooling(title_text):
                areas.add(area)
            continue
        if any(keyword.lower() in file_text for keyword in path_keywords):
            areas.add(area)
            continue
        if any(title_has_keyword(title_text, keyword) for keyword in title_keywords):
            areas.add(area)
    return areas or {"Other"}


def is_docs_tooling_only(files: Iterable[str]) -> bool:
    file_list = [path.lower() for path in files]
    return bool(file_list) and all(is_docs_tooling_path(path) for path in file_list)


def is_docs_tooling_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith("docs/")
        or path.startswith("scripts/")
        or path.startswith("tests/")
        or path.startswith(".github/")
        or "tooling" in path
        or name.startswith("readme")
        or name in {"pytest.ini", "tox.ini", "mypy.ini", "ruff.toml"}
    )


def title_strongly_indicates_docs_tooling(title: str) -> bool:
    words_set = set(words(title))
    phrases = (
        "docs only",
        "documentation only",
        "test only",
        "tests only",
        "tooling only",
        "script only",
        "scripts only",
    )
    return any(phrase in title for phrase in phrases) or bool(
        words_set & {"docs", "documentation", "readme", "tests", "tooling", "scripts"}
    ) and not bool(words_set & {"api", "auth", "route", "runtime", "server", "ui", "memory", "model", "email"})


def title_has_keyword(title: str, keyword: str) -> bool:
    keyword = keyword.lower()
    if " " in keyword:
        return keyword in title
    return keyword in set(words(title))


def hot_files(prs: list[PullRequest]) -> list[tuple[str, list[int]]]:
    owners: dict[str, list[int]] = defaultdict(list)
    for pr in prs:
        for path in pr.files:
            owners[path].append(pr.number)
    rows = [(path, sorted(numbers)) for path, numbers in owners.items() if len(numbers) > 1]
    return sorted(rows, key=lambda row: (-len(row[1]), row[0]))


def overlap_clusters(prs: list[PullRequest]) -> list[list[PullRequest]]:
    by_file: dict[str, list[int]] = defaultdict(list)
    by_number = {pr.number: pr for pr in prs}
    for pr in prs:
        for path in pr.files:
            by_file[path].append(pr.number)

    edges: dict[int, set[int]] = defaultdict(set)
    for numbers in by_file.values():
        if len(numbers) < 2:
            continue
        for number in numbers:
            edges[number].update(n for n in numbers if n != number)

    seen = set()
    clusters = []
    for number in sorted(edges):
        if number in seen:
            continue
        stack = [number]
        cluster_numbers = set()
        while stack:
            current = stack.pop()
            if current in cluster_numbers:
                continue
            cluster_numbers.add(current)
            stack.extend(edges[current] - cluster_numbers)
        seen.update(cluster_numbers)
        clusters.append([by_number[n] for n in sorted(cluster_numbers) if n in by_number])
    return sorted(clusters, key=lambda cluster: (-len(cluster), [pr.number for pr in cluster]))


def score_prs(prs: list[PullRequest], now: datetime | None = None) -> list[ScoredPullRequest]:
    now = now or reference_time(prs)
    file_counts = Counter(path for pr in prs for path in pr.files)
    scored = [score_pr(pr, file_counts, now) for pr in prs]
    return sorted(scored, key=lambda item: (-item.score, item.pr.number))


def score_pr(pr: PullRequest, file_counts: Counter, now: datetime) -> ScoredPullRequest:
    score = 0
    reasons = []
    text = f"{pr.title} {' '.join(pr.files)}".lower()

    # Heuristic, not a truth model: weights favor direct auth/token
    # lifecycle fixes first, then confidentiality/persistence/memory risk,
    # overlap pressure, review state, and actionability. Merge conflicts are
    # caution signals only; they do not prove importance.
    if direct_auth_token_signal(pr):
        score += 45
        reasons.append("direct auth/token lifecycle signal")
    elif any(word in text for word in ("security", "secret", "privilege", "permission")):
        score += 22
        reasons.append("security keyword")

    if any(word in text for word in ("leak", "leaks", "exposure", "cross-user", "cross user", "privacy")):
        score += 18
        reasons.append("data exposure keyword")
    if any(word in text for word in ("data-loss", "persistence", "migration", "database", "sqlite", "postgres")):
        score += 20
        reasons.append("persistence/migration keyword")
    if any(word in text for word in ("memory", "vector", "rag", "embedding", "retrieval")):
        score += 15
        reasons.append("memory/RAG keyword")

    overlap_count = sum(1 for path in pr.files if file_counts[path] > 1)
    if overlap_count:
        points = min(overlap_count * 3, 30)
        score += points
        reasons.append(f"{overlap_count} overlapping file(s)")

    merge_state = pr.merge_state.lower()
    if merge_state in {"clean", "has_hooks"}:
        score += 3
        reasons.append("clean/actionable merge state")
    elif merge_state in {"dirty", "blocked", "conflicting", "unstable"}:
        reasons.append(f"caution: merge state {pr.merge_state}")
    elif merge_state in {"unknown", ""}:
        reasons.append("caution: merge state unknown")

    review_decision = pr.review_decision.lower()
    if review_decision == "approved":
        score -= 8
        reasons.append("already approved")
    elif review_decision == "changes_requested":
        score += 10
        reasons.append("changes requested")
    elif review_decision == "review_required":
        score += 6
        reasons.append("review required")
    elif review_decision in {"unknown", "", "none"}:
        score += 4
        reasons.append("review state unknown")

    age_days = days_since(pr.updated_at, now)
    if age_days is not None and age_days <= 7:
        score += 8
        reasons.append("updated in last 7 days")
    elif age_days is not None and age_days <= 30:
        score += 4
        reasons.append("updated in last 30 days")

    return ScoredPullRequest(pr=pr, score=score, reasons=tuple(reasons or ["low overlap / low signal"]))


def direct_auth_token_signal(pr: PullRequest) -> bool:
    file_text = " ".join(pr.files).lower()
    title = pr.title.lower()
    path_hit = any(
        keyword in file_text
        for keyword in ("auth", "token", "api_key", "api-key", "apikey", "key_manager", "security")
    )
    title_hit = any(
        phrase in title
        for phrase in ("bearer token", "api token", "api key", "auth", "login", "privilege", "permission")
    )
    lifecycle_hit = any(word in title for word in ("deleted", "revoked", "expired", "disabled", "removed"))
    return path_hit and (title_hit or lifecycle_hit)


def days_since(value: str, now: datetime) -> int | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return max((now - parsed).days, 0)


def reference_time(prs: list[PullRequest]) -> datetime:
    parsed = [value for value in (parse_datetime(pr.updated_at) for pr in prs) if value is not None]
    if parsed:
        return max(parsed)
    return datetime.now(timezone.utc)


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def duplicate_candidates(prs: list[PullRequest]) -> list[list[PullRequest]]:
    matches: dict[int, set[int]] = defaultdict(set)
    by_number = {pr.number: pr for pr in prs}
    for index, left in enumerate(prs):
        for right in prs[index + 1 :]:
            if _looks_similar(left, right):
                matches[left.number].add(right.number)
                matches[right.number].add(left.number)
    return _groups_from_matches(matches, by_number)


def _looks_similar(left: PullRequest, right: PullRequest) -> bool:
    left_files = set(left.files)
    right_files = set(right.files)
    if not left_files or not right_files:
        return False
    file_similarity = len(left_files & right_files) / len(left_files | right_files)
    shared_title = title_keywords(left.title) & title_keywords(right.title)
    return file_similarity >= 0.5 and len(shared_title) >= 2


def _groups_from_matches(matches: dict[int, set[int]], by_number: dict[int, PullRequest]) -> list[list[PullRequest]]:
    seen = set()
    groups = []
    for number in sorted(matches):
        if number in seen:
            continue
        stack = [number]
        group = set()
        while stack:
            current = stack.pop()
            if current in group:
                continue
            group.add(current)
            stack.extend(matches[current] - group)
        seen.update(group)
        groups.append([by_number[n] for n in sorted(group) if n in by_number])
    return sorted(groups, key=lambda group: (-len(group), [pr.number for pr in group]))


def words(value: str) -> list[str]:
    return WORD_RE.findall(value.lower())


def title_keywords(title: str) -> set[str]:
    return {word for word in words(title) if len(word) > 2 and word not in STOP_WORDS}


def locked_areas(prs: list[PullRequest], scored: list[ScoredPullRequest]) -> list[dict[str, object]]:
    score_by_number = {item.pr.number: item.score for item in scored}
    rows = []
    for area in ALL_AREAS:
        area_prs = [pr for pr in prs if area in pr.areas]
        if not area_prs:
            continue
        area_files = Counter(path for pr in area_prs for path in pr.files)
        overlapping = [path for path, count in area_files.items() if count > 1]
        max_score = max(score_by_number.get(pr.number, 0) for pr in area_prs)
        missing_files = sum(1 for pr in area_prs if not pr.files)
        priority = _locked_area_priority(area, area_prs, max_score)
        why = _locked_area_why(area, missing_files, len(area_prs), bool(overlapping))
        if missing_files and area != "Other":
            why += "; some PRs have no file metadata"
        rows.append(
            {
                "area": "Other / unclassified" if area == "Other" else area,
                "files": _summarize_files(area_files),
                "prs": [pr.number for pr in sorted(area_prs, key=lambda item: item.number)],
                "why": why,
                "priority": priority,
                "is_other": area == "Other",
            }
        )
    return sorted(rows, key=lambda row: (bool(row["is_other"]), _priority_rank(str(row["priority"])), -len(row["prs"]), str(row["area"])))


def _locked_area_priority(area: str, prs: list[PullRequest], max_score: int) -> str:
    if area == "Other" and all(not pr.files for pr in prs):
        return "watch"
    return "critical" if len(prs) >= 4 or max_score >= 45 else "high" if len(prs) >= 2 or max_score >= 30 else "watch"


def _locked_area_why(area: str, missing_files: int, total_prs: int, has_overlap: bool) -> str:
    if area == "Other" and missing_files > total_prs / 2:
        return f"{total_prs} PRs, mostly missing changed-file metadata"
    return "shared file overlap" if has_overlap else "active open PRs in area"


def _summarize_files(counts: Counter) -> str:
    if not counts:
        return "No changed-file metadata"
    top = [path for path, _count in counts.most_common(5)]
    return ", ".join(top)


def _priority_rank(priority: str) -> int:
    return {"critical": 0, "high": 1, "watch": 2}.get(priority, 3)


def safer_areas(prs: list[PullRequest]) -> list[str]:
    area_counts = Counter(area for pr in prs for area in pr.areas)
    suggestions = []
    for area in ALL_AREAS:
        count = area_counts.get(area, 0)
        if count == 0:
            suggestions.append(f"{area}: no open PRs in this input matched the area mapping")
        elif area == "Docs / tooling / tests" and count <= 2:
            suggestions.append(f"{area}: low overlap; good candidate for docs, tests, or maintenance-only work")
    if not suggestions:
        suggestions.append("No clearly quiet area found; prefer narrow docs, tests, or tooling work after checking current PRs.")
    return suggestions[:6]


def build_structured_report(prs: list[PullRequest], top: int = 15) -> dict:
    top = max(top, 1)
    scored = score_prs(prs)
    hot = hot_files(prs)
    locked = locked_areas(prs, scored)
    duplicates = duplicate_candidates(prs)
    unique_files = len({path for pr in prs for path in pr.files})
    missing_files = missing_file_metadata_count(prs)
    target = scored[0] if scored else None

    return {
        "summary": {
            "highest_risk_areas": _risk_summary(locked),
            "main_overlap_drivers": _overlap_driver_summary(hot),
            "prs_missing_changed_file_metadata": missing_files,
            "recommended_first_review_target": _target_summary(target),
            "total_prs_analyzed": len(prs),
            "unique_files_touched": unique_files,
        },
        "locked_areas": [
            {
                "area": row["area"],
                "files": row["files"],
                "priority": row["priority"],
                "prs": row["prs"],
                "why": row["why"],
            }
            for row in locked
        ],
        "hot_files": [
            {
                "file": path,
                "pr_count": len(numbers),
                "pr_numbers": numbers,
            }
            for path, numbers in hot[:top]
        ],
        "review_priorities": [
            {
                "merge_state": item.pr.merge_state,
                "number": item.pr.number,
                "rank": index,
                "reasons": list(item.reasons),
                "review_decision": item.pr.review_decision,
                "score": item.score,
                "title": item.pr.title or "untitled",
                "url": item.pr.url,
            }
            for index, item in enumerate(scored[:top], start=1)
        ],
        "duplicate_candidates": [
            {
                "pr_numbers": [pr.number for pr in group],
                "titles": [pr.title or "untitled" for pr in group],
            }
            for group in duplicates
        ],
        "safer_areas": safer_areas(prs),
    }


def render_json(prs: list[PullRequest], top: int = 15) -> str:
    return json.dumps(build_structured_report(prs, top), indent=2, sort_keys=True) + "\n"


def render_markdown(prs: list[PullRequest], top: int = 15) -> str:
    top = max(top, 1)
    scored = score_prs(prs)
    hot = hot_files(prs)
    locked = locked_areas(prs, scored)
    duplicates = duplicate_candidates(prs)
    unique_files = len({path for pr in prs for path in pr.files})
    missing_files = missing_file_metadata_count(prs)
    target = scored[0] if scored else None

    lines = ["# PR Blocker Audit", "", "## Executive summary", ""]
    lines.append(f"- Total PRs analyzed: {len(prs)}")
    lines.append(f"- Unique files touched: {unique_files}")
    lines.append(f"- PRs missing changed-file metadata: {missing_files}")
    lines.append(f"- Main overlap drivers: {_overlap_driver_summary(hot)}")
    lines.append(f"- Highest-risk areas: {_risk_summary(locked)}")
    lines.append(f"- Recommended first review target: {_target_summary(target)}")
    lines.extend(["", "## Locked code areas", ""])
    lines.extend(_table(["area", "files/directories", "PRs", "why locked", "priority"], _locked_rows(locked)))
    lines.extend(["", "## Hot files", ""])
    lines.extend(_table(["file", "PR count", "PR numbers"], _hot_rows(hot, top)))
    lines.extend(["", "## Review / blocker priorities", ""])
    lines.append("Heuristic score only; inspect these earlier, do not merge without validation.")
    lines.append("")
    lines.extend(_review_rows(scored, top))
    lines.extend(["", "## Duplicate candidates", ""])
    lines.extend(_duplicate_rows(duplicates))
    lines.extend(["", "## Safer areas for new work", ""])
    lines.extend(f"- {item}" for item in safer_areas(prs))
    lines.append("")
    return "\n".join(lines)


def render_terminal(prs: list[PullRequest], top: int = 15, use_color: bool = False) -> str:
    top = max(top, 1)
    scored = score_prs(prs)
    hot = hot_files(prs)
    locked = locked_areas(prs, scored)
    duplicates = duplicate_candidates(prs)
    unique_files = len({path for pr in prs for path in pr.files})
    missing_files = missing_file_metadata_count(prs)
    target = scored[0] if scored else None

    lines = [colorize("PR Blocker Audit", "bold_cyan", use_color), ""]
    lines.append(f"PRs analyzed: {len(prs)}")
    lines.append(f"Unique files touched: {unique_files}")
    lines.append(f"PRs missing changed-file metadata: {missing_files}")
    lines.append(f"Main overlap drivers: {_overlap_driver_summary(hot)}")
    lines.append(f"Recommended first review target: {_target_summary(target, truncate=True)}")
    lines.extend(["", colorize("Locked areas", "bold_cyan", use_color)])
    if locked:
        for row in locked[:top]:
            priority = str(row["priority"])
            label = colorize(priority.upper(), priority_color(priority), use_color)
            prs_text = _format_pr_numbers(row["prs"])
            lines.append(f"- {label} {row['area']}: {prs_text} ({row['why']})")
            lines.append(colorize(f"  {row['files']}", "dim", use_color))
    else:
        lines.append("- none")

    lines.extend(["", colorize("Hot files", "bold_cyan", use_color)])
    lines.extend(_terminal_hot_rows(hot, top, use_color))
    lines.extend(["", colorize("Review / blocker priorities", "bold_cyan", use_color)])
    lines.append(colorize("Heuristic score only; inspect these first, do not merge without validation.", "dim", use_color))
    if scored:
        for item in scored[:top]:
            pr = item.pr
            state = colorize(pr.merge_state or "unknown", merge_state_color(pr.merge_state), use_color)
            reasons = "; ".join(item.reasons[:3])
            title = shorten_text(pr.title or "untitled")
            lines.append(f"- {item.score:>3}  #{pr.number:<5} {state:<18} {title}")
            lines.append(colorize(f"       {reasons}", "dim", use_color))
    else:
        lines.append("- none")

    lines.extend(["", colorize("Possible duplicates", "bold_cyan", use_color)])
    lines.extend(_terminal_duplicate_rows(duplicates))
    lines.extend(["", colorize("Safer areas", "bold_cyan", use_color)])
    lines.extend(f"- {item}" for item in safer_areas(prs))
    lines.append("")
    return "\n".join(lines)


def _terminal_hot_rows(hot: list[tuple[str, list[int]]], top: int, use_color: bool) -> list[str]:
    if not hot:
        return ["- none"]
    rows = []
    for path, numbers in hot[:top]:
        count_label = f"{len(numbers)} PRs"
        rows.append(f"- {path:<28} {colorize(count_label, hot_count_color(len(numbers)), use_color)}  {_format_pr_numbers(numbers)}")
    return rows


def _terminal_duplicate_rows(groups: list[list[PullRequest]]) -> list[str]:
    if not groups:
        return ["- none detected"]
    rows = []
    for group in groups:
        numbers = _format_pr_numbers(pr.number for pr in group)
        titles = "; ".join(shorten_text(pr.title or "untitled", 80) for pr in group)
        rows.append(f"- Possible duplicate / needs human review: {numbers} - {titles}")
    return rows


def colorize(text: object, style: str, use_color: bool) -> str:
    value = str(text)
    if not use_color:
        return value
    return f"{ANSI[style]}{value}{ANSI['reset']}"


def priority_color(priority: str) -> str:
    return {"critical": "bold_red", "high": "yellow", "watch": "cyan"}.get(priority.lower(), "blue")


def hot_count_color(count: int) -> str:
    return "bold_red" if count >= 4 else "yellow" if count >= 2 else "dim"


def merge_state_color(state: str) -> str:
    normalized = (state or "unknown").lower()
    if normalized == "clean":
        return "green"
    if normalized in {"dirty", "blocked", "conflicting", "unstable"}:
        return "red"
    return "yellow"


def should_use_color(args: argparse.Namespace) -> bool:
    if args.format != "terminal":
        return False
    if args.color == "always":
        if os.name == "nt":
            enable_windows_vt_mode()
        return True
    if args.color == "never" or args.output:
        return False
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt":
        return enable_windows_vt_mode()
    return bool(os.environ.get("TERM") or os.environ.get("COLORTERM"))


def should_show_progress(args: argparse.Namespace) -> bool:
    if args.quiet or args.input or args.no_fetch_files:
        return False
    if args.progress == "always":
        return True
    if args.progress == "never":
        return False
    return sys.stderr.isatty()


def enable_windows_vt_mode() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _cluster_summary(clusters: list[list[PullRequest]]) -> str:
    if not clusters:
        return "none detected"
    summary = []
    for cluster in clusters[:3]:
        summary.append(f"{len(cluster)} PRs ({_format_pr_numbers(pr.number for pr in cluster)})")
    return "; ".join(summary)


def _overlap_driver_summary(hot: list[tuple[str, list[int]]], limit: int = 3) -> str:
    if not hot:
        return "none detected"
    return ", ".join(f"{path} ({len(numbers)} PRs)" for path, numbers in hot[:limit])


def _risk_summary(locked: list[dict[str, object]]) -> str:
    if not locked:
        return "none detected"
    return ", ".join(f"{row['area']} ({row['priority']})" for row in locked[:3])


def _target_summary(target: ScoredPullRequest | None, truncate: bool = False) -> str:
    if target is None:
        return "none; no PRs in input"
    title = target.pr.title or "untitled"
    if truncate:
        title = shorten_text(title)
    return f"PR #{target.pr.number} ({target.score}) - {title}"


def _locked_rows(locked: list[dict[str, object]]) -> list[list[str]]:
    if not locked:
        return [["none", "none", "none", "none", "none"]]
    return [
        [
            str(row["area"]),
            str(row["files"]),
            _format_pr_numbers(row["prs"]),
            str(row["why"]),
            str(row["priority"]),
        ]
        for row in locked
    ]


def _hot_rows(hot: list[tuple[str, list[int]]], top: int) -> list[list[str]]:
    if not hot:
        return [["none", "0", "none"]]
    return [[path, str(len(numbers)), _format_pr_numbers(numbers)] for path, numbers in hot[:top]]


def _review_rows(scored: list[ScoredPullRequest], top: int) -> list[str]:
    if not scored:
        return ["No PRs to rank."]
    lines = []
    for index, item in enumerate(scored[:top], start=1):
        pr = item.pr
        link = f"[#{pr.number}]({pr.url})" if pr.url else f"#{pr.number}"
        reasons = "; ".join(item.reasons)
        lines.append(f"{index}. {link} score {item.score}: {pr.title or 'untitled'} ({reasons})")
    return lines


def _duplicate_rows(groups: list[list[PullRequest]]) -> list[str]:
    if not groups:
        return ["No possible duplicate groups detected from title/file overlap."]
    lines = []
    for group in groups:
        numbers = _format_pr_numbers(pr.number for pr in group)
        titles = "; ".join(f"#{pr.number} {pr.title or 'untitled'}" for pr in group)
        lines.append(f"- Possible duplicate / needs human review: {numbers} - {titles}")
    return lines


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    escaped_headers = [_escape_cell(item) for item in headers]
    lines = ["| " + " | ".join(escaped_headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(item) for item in row) + " |")
    return lines


def _escape_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_pr_numbers(numbers: Iterable[int], limit: int = 12) -> str:
    raw_values = [number for number in numbers if number]
    values = [f"#{number}" for number in raw_values[:limit]]
    if len(raw_values) > limit:
        values.append(f"... (+{len(raw_values) - limit} more)")
    return ", ".join(values) if values else "unknown"


def shorten_text(text: str, max_len: int = 110) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "..."
    return text[: max_len - 3].rstrip() + "..."


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def write_output(report: str, path: str | None) -> None:
    if path:
        Path(path).write_text(ANSI_RE.sub("", report), encoding="utf-8")
        return
    sys.stdout.write(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only audit of open PR file overlap and blocker risk.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Path to JSON from gh pr list --json ... or REST-ish PR payloads")
    source.add_argument("--repo", help="GitHub repository in owner/name form; uses read-only gh commands")
    parser.add_argument("--output", help="Write report to this path instead of stdout")
    parser.add_argument("--limit", type=positive_int, default=1000, help="Live mode: max open PRs to fetch/analyze")
    parser.add_argument("--top", type=positive_int, default=15, help="Rows to show in ranked sections")
    parser.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="Terminal color mode")
    parser.add_argument("--no-color", action="store_const", const="never", dest="color", help="Alias for --color never")
    parser.add_argument("--format", choices=["markdown", "terminal", "json"], default="markdown", help="Output format")
    parser.add_argument("--no-fetch-files", action="store_true", help="Skip per-PR changed-file API calls in live mode")
    parser.add_argument("--progress", choices=["auto", "always", "never"], default="auto", help="Live file-fetch progress mode")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress and non-fatal warning output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.input:
            payload = load_json_file(Path(args.input))
        else:
            progress = ProgressReporter(should_show_progress(args))
            payload = fetch_live_prs(args.repo, fetch_files=not args.no_fetch_files, progress=progress, limit=args.limit)
        prs = normalize_prs(payload)
        missing_files = missing_file_metadata_count(prs)
        if args.repo and not args.no_fetch_files and not args.quiet and missing_files:
            sys.stderr.write(f"{missing_metadata_warning(missing_files)}\n")
        if args.format == "terminal":
            report = render_terminal(prs, top=args.top, use_color=should_use_color(args))
        elif args.format == "json":
            report = render_json(prs, top=args.top)
        else:
            report = render_markdown(prs, top=args.top)
        write_output(report, args.output)
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
