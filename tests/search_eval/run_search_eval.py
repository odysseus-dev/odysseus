#!/usr/bin/env python3
"""Search evaluation harness for Odysseus.

This script runs a fixed query dataset through the real Odysseus search path,
uses an LLM to judge the relevance of returned results, and produces summary
metrics. It is intentionally self-contained and isolated under tests/search_eval/.

Quick start
-----------
    cd /home/blade/Downloads/odysseus
    python tests/search_eval/run_search_eval.py

Environment variables
---------------------
    SEARCH_EVAL_BACKEND       "auto" | "direct" | "http" (default: auto)
    SEARCH_EVAL_TOP_N         number of results to collect per query (default: 5)
    SEARCH_EVAL_DELAY         seconds to sleep between queries (default: 0)
    ODYSSEUS_BASE_URL         when using HTTP backend (default: http://localhost:7000)
    ODYSSEUS_API_TOKEN        optional Bearer token for the HTTP backend
    GRADER_MODEL              model name for the LLM grader (default: gpt-4o-mini)
    GRADER_API_BASE           OpenAI-compatible endpoint (default: https://api.openai.com/v1)
    GRADER_API_KEY            API key for the grader endpoint
                              (falls back to OPENAI_API_KEY or an Odysseus-stored OpenRouter key)
    SKIP_SEARCH               set to "1" to reuse existing tests/search_eval/results.json
    SKIP_GRADING            set to "1" to skip LLM grading and just regenerate report
    SEARCH_EVAL_REPORT_HTML set to "0" to skip the HTML report (default: generated)

Output files
------------
    tests/search_eval/results.json          raw search results for the latest run
    tests/search_eval/graded_results.json   per-query grades for the latest run
    tests/search_eval/report.md             Markdown summary report
    tests/search_eval/report.html           self-contained HTML inspectable report
    tests/search_eval/history.jsonl           append-only record of every evaluation run

The harness can talk to Odysseus in two ways:
    * direct: import services.search.core.searxng_search_results (needs app deps)
    * http:   POST to /api/search/query on a running Odysseus server

"auto" prefers direct import when available; otherwise it falls back to HTTP.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NotRequired, Optional, TypedDict, cast

try:
    from .search_utils import (
    build_failure_reasons,
    empty_metrics,
    extract_json_payload,
    format_results_for_grader,
    QUERY_SCORE_WEIGHT_BEST_TOP_5,
    QUERY_SCORE_WEIGHT_SUCCESS_AT_3,
    QUERY_SCORE_WEIGHT_TOP_1,
    SCORE_MAX,
    USEFUL_SCORE_THRESHOLD,
    append_jsonl_record,
    normalize_grader_scores,
    normalize_search_results,
    parse_bool_env,
    read_json_file,
    read_jsonl_file,
    write_json_file,
    write_text_file,
)
except ImportError:
    from search_utils import (  # type: ignore
        build_failure_reasons,
        empty_metrics,
        extract_json_payload,
        format_results_for_grader,
        QUERY_SCORE_WEIGHT_BEST_TOP_5,
        QUERY_SCORE_WEIGHT_SUCCESS_AT_3,
        QUERY_SCORE_WEIGHT_TOP_1,
        SCORE_MAX,
        USEFUL_SCORE_THRESHOLD,
        append_jsonl_record,
        normalize_grader_scores,
        normalize_search_results,
        parse_bool_env,
        read_json_file,
        read_jsonl_file,
        write_json_file,
        write_text_file,
    )

try:
    from .reporting import generate_html_report, generate_report
except ImportError:
    from reporting import generate_html_report, generate_report  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("search_eval")

# Directories relative to this script
EVAL_DIR = Path(__file__).resolve().parent
ROOT_DIR = EVAL_DIR.parent.parent
QUERIES_FILE = EVAL_DIR / "search_queries.json"
RESULTS_FILE = EVAL_DIR / "results.json"
GRADED_FILE = EVAL_DIR / "graded_results.json"
REPORT_FILE = EVAL_DIR / "report.md"
HTML_REPORT_FILE = EVAL_DIR / "report.html"
HISTORY_FILE = EVAL_DIR / "history.jsonl"


class QueryRecord(TypedDict):
    query: str
    category: str
    expected_topics: NotRequired[List[str]]
    description: NotRequired[str]


class SearchResultItem(TypedDict):
    title: str
    url: str
    snippet: str


class SearchRunRecord(TypedDict):
    query: str
    category: str
    expected_topics: NotRequired[List[str]]
    description: NotRequired[str]
    top_n: int
    results: List[SearchResultItem]
    result_count: int
    backend: NotRequired[str]


class GradedRecord(TypedDict):
    query: str
    category: str
    description: str
    result_count: int
    scores: List[int]
    average_score: float
    top_result_score: int
    best_score_top_3: int
    best_score_top_5: int
    success_at_1: bool
    success_at_3: bool
    success_at_5: bool
    catastrophic_failure: bool
    failure_reasons: List[str]
    query_score: float
    useful_threshold: int
    grader_flags: List[Any]
    grader_reasoning: str
    grader_error: Optional[str]
    grader_raw_response: Optional[str]


@dataclass(frozen=True)
class SearchEvalConfig:
    backend: str
    top_n: int
    skip_search: bool
    skip_grading: bool
    grader_model: str
    grader_api_base: str
    grader_api_key: str
    delay: float
    grade_limit: int
    report_html: bool

    @classmethod
    def from_argv(cls, argv: Optional[List[str]] = None) -> "SearchEvalConfig":
        odys_defaults = load_odysseus_endpoint_defaults()
        parser = argparse.ArgumentParser(
            description="Run the Odysseus search relevance evaluation harness.",
        )
        parser.add_argument(
            "--backend",
            choices=["auto", "direct", "http"],
            default=None,
            help="How to reach Odysseus search (default: auto)",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=None,
            help="Number of results to collect per query (default: 5)",
        )
        parser.add_argument(
            "--skip-search",
            action="store_true",
            default=None,
            help="Reuse existing results.json and skip running searches",
        )
        parser.add_argument(
            "--skip-grading",
            action="store_true",
            default=None,
            help="Skip LLM grading and only aggregate/report",
        )
        parser.add_argument(
            "--grader-model",
            default=None,
            help="LLM model used to grade relevance",
        )
        parser.add_argument(
            "--grader-api-base",
            default=None,
            help="OpenAI-compatible base URL",
        )
        parser.add_argument(
            "--grader-api-key",
            default=None,
            help="API key for the grader endpoint (falls back to OPENAI_API_KEY or Odysseus stored key)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=None,
            help="Seconds to sleep between search queries (default: 0.2)",
        )
        parser.add_argument(
            "--grade-limit",
            type=int,
            default=None,
            help="Grade only the first N queries; 0 means grade all (default: 0)",
        )
        parser.add_argument(
            "--report-html",
            dest="report_html",
            action="store_true",
            default=None,
            help="Generate a self-contained HTML report (default: True)",
        )
        parser.add_argument(
            "--no-report-html",
            dest="report_html",
            action="store_false",
            help="Skip generating the HTML report",
        )
        args = parser.parse_args(argv)

        report_html_env = os.environ.get("SEARCH_EVAL_REPORT_HTML", "1").lower()
        report_html_default = report_html_env not in ("0", "false", "no")

        skip_search_arg = cast(Optional[bool], args.skip_search)
        skip_grading_arg = cast(Optional[bool], args.skip_grading)
        report_html_arg = cast(Optional[bool], args.report_html)

        return cls(
            backend=args.backend or os.environ.get("SEARCH_EVAL_BACKEND", "auto"),
            top_n=args.top_n if args.top_n is not None else int(os.environ.get("SEARCH_EVAL_TOP_N", 5)),
            skip_search=skip_search_arg if skip_search_arg is not None else parse_bool_env("SKIP_SEARCH", default=False),
            skip_grading=skip_grading_arg if skip_grading_arg is not None else parse_bool_env("SKIP_GRADING", default=False),
            grader_model=args.grader_model or os.environ.get("GRADER_MODEL") or odys_defaults.get("model") or "openai/gpt-4.1-mini",
            grader_api_base=args.grader_api_base or os.environ.get("GRADER_API_BASE") or odys_defaults.get("api_base") or "https://api.openai.com/v1",
            grader_api_key=args.grader_api_key or os.environ.get("GRADER_API_KEY") or os.environ.get("OPENAI_API_KEY") or odys_defaults.get("api_key", ""),
            delay=args.delay if args.delay is not None else float(os.environ.get("SEARCH_EVAL_DELAY", 0.2)),
            grade_limit=args.grade_limit if args.grade_limit is not None else int(os.environ.get("SEARCH_EVAL_GRADE_LIMIT", 0)),
            report_html=report_html_arg if report_html_arg is not None else report_html_default,
        )


# ---------------------------------------------------------------------------
# Backend adapters: run a single query through the real Odysseus search path.
# ---------------------------------------------------------------------------

class SearchBackend:
    async def search(self, query: str, top_n: int) -> List[SearchResultItem]:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


class DirectBackend(SearchBackend):
    """Use the same Python function the app uses internally.

    Exercises provider selection, query routing, retrieval and the
    rank_search_results reranker without fetching full page content.
    """

    def __init__(self):
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self):
        if self._fn is not None:
            return
        # Insert project root into path so the Odysseus package imports work.
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))
        from services.search.core import searxng_search_results  # noqa: I001
        self._fn = searxng_search_results

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    async def search(self, query: str, top_n: int) -> List[SearchResultItem]:
        self._load()
        fn = cast(Callable[..., Any], self._fn)
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, fn, query, top_n)
        except Exception as exc:
            logger.error("direct search failed for %r: %s", query, exc)
            return []
        return cast(List[SearchResultItem], normalize_search_results(raw, top_n))


class HTTPBackend(SearchBackend):
    """Call POST /api/search/query on a running Odysseus server."""

    def __init__(self, base_url: str, api_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token

    async def search(self, query: str, top_n: int) -> List[SearchResultItem]:
        import httpx

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        url = f"{self.base_url}/api/search/query"
        payload = {"query": query, "count": top_n}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=60.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("http search failed for %r: %s", query, exc)
            return []

        raw = data.get("results", []) if isinstance(data, dict) else []
        return cast(List[SearchResultItem], normalize_search_results(raw, top_n))


def choose_backend(preferred: str) -> SearchBackend:
    """Pick the best available backend."""
    if preferred == "http":
        base_url = os.environ.get("ODYSSEUS_BASE_URL", "http://localhost:7000")
        api_token = os.environ.get("ODYSSEUS_API_TOKEN", "")
        return HTTPBackend(base_url, api_token)

    if preferred == "direct":
        return DirectBackend()

    # Auto: try direct import first, then HTTP probe.
    direct = DirectBackend()
    if direct.is_available():
        logger.info("auto-selected direct backend")
        return direct

    logger.info("direct backend unavailable")

    base_url = os.environ.get("ODYSSEUS_BASE_URL", "http://localhost:7000")
    api_token = os.environ.get("ODYSSEUS_API_TOKEN", "")
    logger.info("auto-selected http backend: %s", base_url)
    return HTTPBackend(base_url, api_token)


# ---------------------------------------------------------------------------
# Default grader from Odysseus ModelEndpoint database
# ---------------------------------------------------------------------------

def load_odysseus_endpoint_defaults() -> Dict[str, str]:
    """Look up an enabled OpenRouter endpoint from data/app.db.

    Returns a dict with keys: api_base, api_key, model._EMPTY strings if no
    suitable endpoint is found.
    """
    defaults: Dict[str, str] = {"api_base": "", "api_key": "", "model": ""}
    try:
        # Imports are deferred so the harness still starts if app deps are missing.
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))
        from core.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = (
                db.query(ModelEndpoint)
                .filter(
                    ModelEndpoint.is_enabled == True,  # noqa: E712
                    (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
                )
                .filter(
                    ModelEndpoint.name.ilike("%openrouter%")
                    | ModelEndpoint.base_url.ilike("%openrouter%")
                )
                .first()
            )
            if ep:
                base_url = ep.base_url
                if not base_url.endswith("/v1"):
                    base_url = base_url.rstrip("/") + "/v1"
                defaults["api_base"] = base_url
                defaults["api_key"] = ep.api_key or ""
                defaults["model"] = "openai/gpt-4o-mini"
                logger.info("Found OpenRouter endpoint in DB: %s (%s)", ep.name, ep.base_url)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Could not load OpenRouter endpoint from Odysseus DB: %s", exc)
    return defaults


# ---------------------------------------------------------------------------
# LLM relevance grader
# ---------------------------------------------------------------------------

GRADING_PROMPT = """You are a strict search-relevance judge for the Odysseus repository.

User query: {query}
Query category: {category}
Likely user intent: {intent}

Grade each search result on a 0-4 scale for how well it satisfies the user's intent.

0 = irrelevant, wrong project, wrong Odysseus, wrong subsystem, spam, or misleading.
1 = weakly related keyword match, unlikely to help the user.
2 = partially useful, related but broad, indirect, or incomplete.
3 = useful, likely helps answer the query.
4 = excellent, directly answers the query or clearly points to the exact file/page/command/fix/feature.

Important grading rules:
- Grade intent match, not keyword overlap.
- Be strict.
- Wrong Odysseus/project/domain must score 0.
- Correct project but wrong subsystem can score at most 1.
- Generic homepage, repo root, or broad product overview can score at most 2 unless it directly answers the query.
- Keyword match without intent match can score at most 2.
- For exact lookup queries, only give 4 if the result gives or clearly points to the exact file/path/page/location.

Return ONLY a valid JSON object in this exact shape:
{{"scores": [0, 1, 2, 3, 4], "flags": [], "reasoning": "brief one-line summary"}}

Results to grade:
{results}
"""


class LLMGrader:
    def __init__(self, model: str, api_base: str, api_key: str):
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    async def grade(
        self,
        query: str,
        intent: str,
        results: List[SearchResultItem],
        category: str = "unknown",
    ) -> Dict[str, Any]:
        import httpx

        prompt = GRADING_PROMPT.format(
            query=query,
            category=category or "unknown",
            intent=intent,
            results=format_results_for_grader(cast(List[Dict[str, Any]], results)),
        )
        messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 512,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.api_base}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("grader API call failed for %r: %s", query, exc)
            return {"error": str(exc), "scores": []}

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = extract_json_payload(content)
        if not parsed or "scores" not in parsed:
            logger.warning("Could not parse grader response for %r: %s", query, content)
            return {"raw_response": content, "scores": []}

        flags = parsed.get("flags", [])
        if not isinstance(flags, list):
            flags = []

        return {
            "scores": parsed.get("scores", []),
            "flags": flags,
            "reasoning": parsed.get("reasoning", ""),
        }


# ---------------------------------------------------------------------------
# Search phase
# ---------------------------------------------------------------------------

async def run_search_phase(
    queries: List[QueryRecord],
    backend: SearchBackend,
    top_n: int,
    delay: float = 0.0,
) -> List[SearchRunRecord]:
    results: List[SearchRunRecord] = []
    for idx, q in enumerate(queries, 1):
        logger.info("[%d/%d] searching: %s", idx, len(queries), q["query"])
        raw = await backend.search(q["query"], top_n)
        results.append({
            "query": q["query"],
            "category": q["category"],
            "expected_topics": q.get("expected_topics", []),
            "description": q.get("description", ""),
            "top_n": top_n,
            "results": raw,
            "result_count": len(raw),
        })
        if delay > 0 and idx < len(queries):
            await asyncio.sleep(delay)
    return results


# ---------------------------------------------------------------------------
# Grading phase
# ---------------------------------------------------------------------------

async def run_grading_phase(results: List[SearchRunRecord], grader: LLMGrader) -> List[GradedRecord]:
    graded: List[GradedRecord] = []
    for idx, item in enumerate(results, 1):
        query = item["query"]
        category = item.get("category", "unknown")
        logger.info("[%d/%d] grading: %s", idx, len(results), query)
        grader_result = await grader.grade(
            query,
            item.get("description", ""),
            item.get("results", []),
            category=category,
        )
        target_count = item.get("result_count", 0)
        scores = normalize_grader_scores(grader_result.get("scores", []), target_count)

        avg = sum(scores) / len(scores) if scores else 0.0
        top_result_score = scores[0] if scores else 0

        top_3 = scores[:3]
        top_5 = scores[:5]
        best_score_top_3 = max(top_3) if top_3 else 0
        best_score_top_5 = max(top_5) if top_5 else 0
        success_at_1 = top_result_score >= USEFUL_SCORE_THRESHOLD
        success_at_3 = any(score >= USEFUL_SCORE_THRESHOLD for score in top_3)
        success_at_5 = any(score >= USEFUL_SCORE_THRESHOLD for score in top_5)

        failure_reasons = build_failure_reasons(
            category=category,
            target_count=target_count,
            top_result_score=top_result_score,
            top_3_scores=top_3,
            success_at_5=success_at_5,
        )

        catastrophic_failure = bool(failure_reasons)

        query_score = (
            QUERY_SCORE_WEIGHT_TOP_1 * (top_result_score / SCORE_MAX)
            + QUERY_SCORE_WEIGHT_SUCCESS_AT_3 * (1 if success_at_3 else 0)
            + QUERY_SCORE_WEIGHT_BEST_TOP_5 * (best_score_top_5 / SCORE_MAX)
        )

        graded.append({
            "query": query,
            "category": category,
            "description": item.get("description", ""),
            "result_count": target_count,
            "scores": scores,
            "average_score": round(avg, 2),
            "top_result_score": top_result_score,
            "best_score_top_3": best_score_top_3,
            "best_score_top_5": best_score_top_5,
            "success_at_1": success_at_1,
            "success_at_3": success_at_3,
            "success_at_5": success_at_5,
            "catastrophic_failure": catastrophic_failure,
            "failure_reasons": failure_reasons,
            "query_score": round(query_score, 1),
            "useful_threshold": USEFUL_SCORE_THRESHOLD,
            "grader_flags": grader_result.get("flags", []) if isinstance(grader_result.get("flags", []), list) else [],
            "grader_reasoning": grader_result.get("reasoning", ""),
            "grader_error": grader_result.get("error"),
            "grader_raw_response": grader_result.get("raw_response"),
        })
    return graded


# ---------------------------------------------------------------------------
# Aggregation / reporting
# ---------------------------------------------------------------------------

def compute_metrics(graded: List[GradedRecord]) -> Dict[str, Any]:
    if not graded:
        return empty_metrics()

    # Per-query overall score (already average_score) and per-category.
    overall_scores = [g["average_score"] for g in graded]
    top1_scores = [g["top_result_score"] for g in graded]
    top3_scores = []
    for g in graded:
        scores = g.get("scores", [])
        if scores:
            top3_scores.append(sum(scores[:3]) / min(3, len(scores)))

    query_scores = [float(g.get("query_score", 0.0)) for g in graded]
    success1 = [1 if g.get("success_at_1") else 0 for g in graded]
    success3 = [1 if g.get("success_at_3") else 0 for g in graded]
    success5 = [1 if g.get("success_at_5") else 0 for g in graded]
    catastrophic = [1 if g.get("catastrophic_failure") else 0 for g in graded]

    category_buckets: Dict[str, List[float]] = defaultdict(list)
    for g in graded:
        category_buckets[g["category"]].append(g["average_score"])

    per_category = {}
    for cat, scores in category_buckets.items():
        rows = [g for g in graded if g.get("category") == cat]
        per_category[cat] = {
            "count": len(scores),
            "average": round(sum(scores) / len(scores), 2),
            "query_score": round(sum(float(r.get("query_score", 0.0)) for r in rows) / len(rows), 1),
            "success_at_3": round(sum(1 if r.get("success_at_3") else 0 for r in rows) / len(rows), 3),
        }

    # Sort worst first; include ties with average_score <= 1.0 or bottom 10.
    sorted_by_score = sorted(graded, key=lambda x: (float(x.get("query_score", 0.0)), x.get("average_score", 0.0)))
    worst_queries = [g for g in sorted_by_score if float(g.get("query_score", 0.0)) <= 40.0][:10]
    if not worst_queries:
        worst_queries = sorted_by_score[:10]

    return {
        "average_relevance": round(sum(overall_scores) / len(overall_scores), 2),
        "top1_relevance": round(sum(top1_scores) / len(top1_scores), 2) if top1_scores else 0.0,
        "top3_relevance": round(sum(top3_scores) / len(top3_scores), 2) if top3_scores else 0.0,
        "average_query_score": round(sum(query_scores) / len(query_scores), 1),
        "success_at_1": round(sum(success1) / len(success1), 3),
        "success_at_3": round(sum(success3) / len(success3), 3),
        "success_at_5": round(sum(success5) / len(success5), 3),
        "catastrophic_failure_rate": round(sum(catastrophic) / len(catastrophic), 3),
        "per_category": per_category,
        "worst_queries": worst_queries,
    }


# ---------------------------------------------------------------------------
# Run history helpers
# ---------------------------------------------------------------------------

def build_history_record(
    run_timestamp: str,
    backend_name: str,
    model: Optional[str],
    results: List[SearchRunRecord],
    graded: List[GradedRecord],
    metrics: Dict[str, Any],
    grading_skipped: bool,
    top_n: int,
) -> Dict[str, Any]:
    """Build a compact, append-only record describing this evaluation run."""
    return {
        "timestamp": run_timestamp,
        "backend": backend_name,
        "grader_model": model,
        "queries_run": len(results),
        "top_n": top_n,
        "grading_skipped": grading_skipped,
        "metrics": {
            "average_query_score": metrics.get("average_query_score", 0.0),
            "average_relevance": metrics.get("average_relevance", 0.0),
            "top1_relevance": metrics.get("top1_relevance", 0.0),
            "top3_relevance": metrics.get("top3_relevance", 0.0),
            "success_at_1": metrics.get("success_at_1", 0.0),
            "success_at_3": metrics.get("success_at_3", 0.0),
            "success_at_5": metrics.get("success_at_5", 0.0),
            "catastrophic_failure_rate": metrics.get("catastrophic_failure_rate", 0.0),
            "per_category": metrics.get("per_category", {}),
        },
        "per_query": [
            {
                "query": g["query"],
                "category": g.get("category", "unknown"),
                "result_count": g.get("result_count", 0),
                "average_score": g.get("average_score", 0.0),
                "query_score": g.get("query_score", 0.0),
                "top_result_score": g.get("top_result_score", 0),
                "success_at_1": g.get("success_at_1", False),
                "success_at_3": g.get("success_at_3", False),
                "success_at_5": g.get("success_at_5", False),
                "failure_reasons": g.get("failure_reasons", []),
            }
            for g in graded
        ] if graded else [],
    }


def load_history() -> List[Dict[str, Any]]:
    """Read all previous run records from the JSONL history file."""
    return read_jsonl_file(
        HISTORY_FILE,
        on_decode_error=lambda exc: logger.warning("Skipping malformed history line: %s", exc),
    )


def load_query_records() -> List[QueryRecord]:
    raw_queries = read_json_file(QUERIES_FILE)
    return cast(List[QueryRecord], raw_queries)


def load_search_results() -> List[SearchRunRecord]:
    raw_results = read_json_file(RESULTS_FILE)
    return cast(List[SearchRunRecord], raw_results)


def load_graded_results() -> List[GradedRecord]:
    raw_graded = read_json_file(GRADED_FILE)
    return cast(List[GradedRecord], raw_graded)


def append_history(record: Dict[str, Any]) -> None:
    """Append a single run record to the JSONL history file."""
    append_jsonl_record(HISTORY_FILE, record)


async def main(argv: Optional[List[str]] = None) -> int:
    config = SearchEvalConfig.from_argv(argv)

    if not QUERIES_FILE.exists():
        logger.error("Query dataset not found: %s", QUERIES_FILE)
        return 1

    queries = load_query_records()
    logger.info("Loaded %d queries from %s", len(queries), QUERIES_FILE)
    results: List[SearchRunRecord] = []
    graded: List[GradedRecord] = []
    backend_name = "unknown"
    model_name: Optional[str] = None

    if (
        config.skip_search
        and config.skip_grading
        and not RESULTS_FILE.exists()
        and not GRADED_FILE.exists()
    ):
        print("No results found. Please run the search first.")
        return 1

    # ------------------------------------------------------------------
    # Search phase
    # ------------------------------------------------------------------
    if config.skip_search:
        logger.info("Skipping search as requested.")
        if RESULTS_FILE.exists():
            logger.info("Reusing existing search results from %s", RESULTS_FILE)
            results = load_search_results()
            backend_name = results[0].get("backend", "unknown") if results else "unknown"
    else:
        backend = choose_backend(config.backend)
        backend_name = type(backend).__name__.replace("Backend", "").lower()
        logger.info("Running search with backend=%s, top_n=%d, delay=%.1f", backend_name, config.top_n, config.delay)
        results = await run_search_phase(queries, backend, config.top_n, delay=config.delay)
        for item in results:
            item["backend"] = backend_name
            item["top_n"] = config.top_n
        write_json_file(RESULTS_FILE, results)
        logger.info("Wrote raw results to %s", RESULTS_FILE)

    # ------------------------------------------------------------------
    # Grading phase
    # ------------------------------------------------------------------
    if config.skip_grading:
        logger.info("Skipping LLM grading as requested.")
        if GRADED_FILE.exists():
            logger.info("Reusing existing graded results from %s", GRADED_FILE)
            graded = load_graded_results()
    else:
        if not results and RESULTS_FILE.exists():
            results = load_search_results()
            backend_name = results[0].get("backend", "unknown") if results else backend_name
        if not results and not RESULTS_FILE.exists():
            print("No results to grade. Run the search first or remove --skip-search.")
            return 1

        api_key = config.grader_api_key
        if not api_key:
            logger.warning(
                "GRADER_API_KEY not set; grader calls will likely fail. "
                "Set it or use --skip-grading to generate a report without scores.",
            )
        grader = LLMGrader(
            model=config.grader_model,
            api_base=config.grader_api_base,
            api_key=api_key,
        )
        model_name = config.grader_model
        grade_limit = config.grade_limit
        if grade_limit > 0:
            logger.info("Limiting grading to first %d queries", grade_limit)
            results = results[:grade_limit]
        graded = await run_grading_phase(results, grader)
        write_json_file(GRADED_FILE, graded)
        logger.info("Wrote graded results to %s", GRADED_FILE)

    if not results and RESULTS_FILE.exists():
        results = load_search_results()
        backend_name = results[0].get("backend", "unknown") if results else backend_name

    # ------------------------------------------------------------------
    # Reporting phase
    # ------------------------------------------------------------------
    run_timestamp = datetime.now(timezone.utc).isoformat()
    grading_skipped = config.skip_grading and not bool(graded)

    if graded:
        metrics = compute_metrics(graded)
    else:
        metrics = empty_metrics()

    history_record = build_history_record(
        run_timestamp,
        backend_name,
        model_name,
        results,
        graded,
        metrics,
        grading_skipped,
        config.top_n,
    )
    append_history(history_record)
    prior_history = [h for h in load_history() if h.get("timestamp") != run_timestamp][-10:][::-1]

    report = generate_report(
        cast(List[Dict[str, Any]], graded),
        cast(List[Dict[str, Any]], results),
        metrics,
        backend_name,
        model_name,
        grading_skipped=grading_skipped,
        run_timestamp=run_timestamp,
        history=prior_history,
    )
    write_text_file(REPORT_FILE, report)

    logger.info("Wrote report to %s", REPORT_FILE)

    if config.report_html:
        html_report = generate_html_report(
            cast(List[Dict[str, Any]], graded),
            cast(List[Dict[str, Any]], results),
            metrics,
            backend_name,
            model_name,
            grading_skipped=grading_skipped,
            run_timestamp=run_timestamp,
        )
        write_text_file(HTML_REPORT_FILE, html_report)
        logger.info("Wrote HTML report to %s", HTML_REPORT_FILE)

    # Print a compact summary to the console.
    print("\n" + "=" * 60)
    print("Search Evaluation Complete")
    print("=" * 60)
    print(f"Backend:        {backend_name}")
    if model_name:
        print(f"Grader model:   {model_name}")
    print(f"Queries run:    {len(results)}")
    print(f"Timestamp:      {run_timestamp}")
    if graded:
        print(f"Avg query score:{metrics['average_query_score']} / 100")
        print(f"Success@1:      {metrics['success_at_1']}")
        print(f"Success@3:      {metrics['success_at_3']}")
        print(f"Success@5:      {metrics['success_at_5']}")
        print(f"Avg score (diag){metrics['average_relevance']} / {SCORE_MAX}")
    print(f"Report:         {REPORT_FILE}")
    if config.report_html:
        print(f"HTML report:    {HTML_REPORT_FILE}")
    print(f"History:        {HISTORY_FILE}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
