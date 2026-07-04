from __future__ import annotations

import html
from pathlib import Path
from string import Template
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .search_utils import read_text_file
except ImportError:
    from search_utils import read_text_file  # type: ignore

EVAL_DIR = Path(__file__).resolve().parent
HTML_TEMPLATE_FILE = EVAL_DIR / "report_template.html"


def generate_report(
    graded: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    backend_name: str,
    model: Optional[str],
    grading_skipped: bool,
    run_timestamp: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    now = run_timestamp or datetime.now(timezone.utc).isoformat()
    lines = [
        "# Search Evaluation Report",
        "",
        f"Generated: {now}",
        f"Backend: `{backend_name}`",
        f"Grader model: `{model or 'not used'}`",
        f"Queries run: {len(results)}",
    ]
    if grading_skipped:
        lines.append("Grading: **skipped** (no LLM scores available)")
    else:
        lines.append(f"Queries graded: {len(graded)}")
    lines.extend([
        "",
        "## Summary",
        "",
    ])
    if grading_skipped:
        lines.append("_Grading was skipped, so no relevance scores are available._")
        lines.append("")
    else:
        lines.extend([
            f"- Average query score (primary): **{metrics['average_query_score']}** / 100",
            f"- Success@1 (useful result): **{metrics['success_at_1']}**",
            f"- Success@3 (useful result): **{metrics['success_at_3']}**",
            f"- Success@5 (useful result): **{metrics['success_at_5']}**",
            f"- Catastrophic failure rate: **{metrics['catastrophic_failure_rate']}**",
            "",
            "Diagnostic metrics:",
            f"- Average relevance score: **{metrics['average_relevance']}** (scale 0-4)",
            f"- Top-1 relevance: **{metrics['top1_relevance']}**",
            f"- Top-3 average relevance: **{metrics['top3_relevance']}**",
            "",
        ])
    lines.extend([
        "Interpretation:",
        "- 0 = irrelevant",
        "- 1 = weakly related",
        "- 2 = partially useful",
        "- 3 = useful",
        "- 4 = excellent",
        "- useful threshold: score >= 3",
        "",
        "## Score by Category",
        "",
        "| Category | Count | Avg Score | Avg Query Score | Success@3 |",
        "|----------|------:|----------:|----------------:|----------:|",
    ])

    for cat, info in sorted(metrics["per_category"].items()):
        lines.append(
            f"| {cat} | {info['count']} | {info['average']} | {info.get('query_score', 0.0)} | {info.get('success_at_3', 0.0)} |"
        )

    lines.extend([
        "",
        "## Worst Queries",
        "",
        "These queries returned the least relevant results and are the best candidates",
        "for focused search improvements.",
        "",
        "| Query | Category | Query Score | Avg Score | Top Score | Failure Reasons |",
        "|-------|----------|------------:|----------:|----------:|-----------------|",
    ])

    for w in metrics["worst_queries"]:
        reasons = ", ".join(w.get("failure_reasons", [])) or "-"
        lines.append(
            f"| `{w['query']}` | {w['category']} | {w.get('query_score', 0.0)} | {w['average_score']} | {w['top_result_score']} | {reasons} |"
        )

    lines.extend([
        "",
        "## Example Bad Results",
        "",
        "Queries where the top returned result scored 0 or 1.",
        "",
    ])

    bad_examples = [g for g in graded if g["top_result_score"] <= 1]
    if not bad_examples:
        lines.append("_No bad top results found._")
    else:
        for g in bad_examples[:10]:
            lines.append(f"### `{g['query']}` ({g['category']})")
            lines.append("")
            lines.append(f"- **Average score:** {g['average_score']}")
            lines.append(f"- **Query score:** {g.get('query_score', 0.0)}")
            lines.append(f"- **Top score:** {g['top_result_score']}")
            lines.append(f"- **Best top-3 score:** {g.get('best_score_top_3', 0)}")
            lines.append(f"- **Best top-5 score:** {g.get('best_score_top_5', 0)}")
            lines.append(f"- **Success@3:** {g.get('success_at_3', False)}")
            if g.get("failure_reasons"):
                lines.append(f"- **Failure reasons:** {', '.join(g.get('failure_reasons', []))}")
            lines.append(f"- **Description:** {g.get('description', '')}")
            lines.append("- See `results.json` for the full returned results.")
            if g.get("grader_reasoning"):
                lines.append(f"- **Grader note:** {g['grader_reasoning']}")
            lines.append("")

    lines.extend([
        "",
        "## Distribution",
        "",
    ])
    buckets = {
        "irrelevant (0.0-0.5)": 0,
        "weak (0.5-1.5)": 0,
        "partially useful (1.5-2.5)": 0,
        "useful or better (2.5-4.0)": 0,
    }
    for g in graded:
        score = g["average_score"]
        if score <= 0.5:
            buckets["irrelevant (0.0-0.5)"] += 1
        elif score <= 1.5:
            buckets["weak (0.5-1.5)"] += 1
        elif score <= 2.5:
            buckets["partially useful (1.5-2.5)"] += 1
        else:
            buckets["useful or better (2.5-4.0)"] += 1

    for label, count in buckets.items():
        lines.append(f"- {label}: {count}")

    if history:
        lines.extend([
            "",
            "## Run History",
            "",
            "Recent evaluation runs from `history.jsonl` (most recent first).",
            "",
            "| Timestamp | Backend | Queries | Avg Query Score | Success@3 | Success@5 | Catastrophic Failures |",
            "|-----------|---------|--------:|----------------:|----------:|----------:|----------------------:|",
        ])
        for h in history[:10]:
            h_metrics = h.get("metrics", {})
            lines.append(
                f"| {h.get('timestamp', '')} "
                f"| {h.get('backend', '')} "
                f"| {h.get('queries_run', 0)} "
                f"| {h_metrics.get('average_query_score', 0.0)} "
                f"| {h_metrics.get('success_at_3', 0.0)} "
                f"| {h_metrics.get('success_at_5', 0.0)} "
                f"| {h_metrics.get('catastrophic_failure_rate', 0.0)} |"
            )
        lines.append("")

    return "\n".join(lines)


def generate_html_report(
    graded: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    backend_name: str,
    model: Optional[str],
    grading_skipped: bool,
    run_timestamp: Optional[str] = None,
) -> str:
    now = run_timestamp or datetime.now(timezone.utc).isoformat()

    def _score_class(score: int) -> str:
        return f"score-{min(4, max(0, int(score)))}"

    def _metric_card(label: str, value: Any) -> str:
        return (
            f'<div class="metric">'
            f'<div class="metric-value">{html.escape(str(value))}</div>'
            f'<div class="metric-label">{html.escape(label)}</div>'
            f"</div>"
        )

    graded_by_query = {g["query"]: g for g in graded}
    categories = sorted({g.get("category", "unknown") for g in graded})
    category_options = "\n".join(
        f'<option value="{html.escape(cat)}">{html.escape(cat)}</option>' for cat in categories
    )
    ordered_results = sorted(
        results,
        key=lambda r: float((graded_by_query.get(r["query"]) or {}).get("query_score", 0.0)),
    )

    query_cards: List[str] = []
    for r in ordered_results:
        query = r["query"]
        g = graded_by_query.get(query)
        if not g:
            continue

        result_rows: List[str] = []
        raw_results = r.get("results", []) or []
        scores = g.get("scores", []) or []
        for idx, item in enumerate(raw_results, 1):
            score = int(scores[idx - 1]) if idx - 1 < len(scores) else -1
            score_text = str(score) if score >= 0 else "—"
            title = html.escape(item.get("title") or "no title")
            url = html.escape(item.get("url") or "")
            snippet = html.escape(item.get("snippet") or "")
            result_rows.append(
                f'<div class="result-row">'
                f'<div class="rank">{idx}</div>'
                f"<div>"
                f'<div class="result-title">'
                f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
                f"</div>"
                f'<div class="result-url">{url}</div>'
                f'<div class="result-snippet">{snippet}</div>'
                f'<div class="result-meta">'
                f'<span class="score-badge {_score_class(score)}">score: {score_text}</span>'
                f"</div>"
                f"</div>"
                f"</div>"
            )

        failures = g.get("failure_reasons", []) or []
        failure_badges = "".join(
            f'<span class="badge failure">{html.escape(reason)}</span>' for reason in failures
        )
        reasoning = g.get("grader_reasoning") or ""
        reasoning_html = f'<div class="reason">Grader: {html.escape(reasoning)}</div>' if reasoning else ""

        query_cards.append(
            f'<div class="query-card" '
            f'data-category="{html.escape(g.get("category", "unknown"))}" '
            f'data-failures="{"true" if failures else "false"}">'
            f'<div class="query-header">'
            f"<div>"
            f'<div class="query-title">{html.escape(query)}</div>'
            f'<div class="badges">'
            f'<span class="badge category">{html.escape(g.get("category", "unknown"))}</span>'
            f'<span class="badge">avg {g.get("average_score", 0)}</span>'
            f'<span class="badge">top {g.get("top_result_score", 0)}</span>'
            f"{failure_badges}"
            f"</div>"
            f"</div>"
            f'<div class="metric-value">{g.get("query_score", 0)}</div>'
            f"</div>"
            f'<div class="results">'
            f'{"".join(result_rows)}'
            f"{reasoning_html}"
            f"</div>"
            f"</div>"
        )

    if grading_skipped:
        summary_cards = _metric_card("Queries", len(results))
    else:
        summary_cards = "".join([
            _metric_card("Queries", len(results)),
            _metric_card("Avg query score", f"{metrics.get('average_query_score', 0.0)} / 100"),
            _metric_card("Success @1", metrics.get("success_at_1", 0.0)),
            _metric_card("Success @3", metrics.get("success_at_3", 0.0)),
            _metric_card("Success @5", metrics.get("success_at_5", 0.0)),
            _metric_card("Avg relevance", f"{metrics.get('average_relevance', 0.0)} / 4"),
            _metric_card("Catastrophic failures", metrics.get("catastrophic_failure_rate", 0.0)),
        ])

    html_cards = "\n".join(query_cards)
    subtitle = (
        f"{html.escape(now)} · Backend: {html.escape(backend_name)} "
        f"· Grader model: {html.escape(model or 'not used')}"
    )
    query_cards_content = html_cards or '<div class="empty">No graded results to display.</div>'
    template = Template(read_text_file(HTML_TEMPLATE_FILE))
    return template.substitute(
        subtitle=subtitle,
        summary_cards=summary_cards,
        category_options=category_options,
        query_cards=query_cards_content,
    )
