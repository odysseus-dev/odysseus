# AI Search Testing Framework

This framework runs a fixed query set through Odysseus search, grades returned results with an LLM relevance judge, computes quality metrics, and saves both one-run artifacts and long-term history so search changes can be compared over time.

## File map

- `run_search_eval.py`: Main harness entrypoint. Loads config/queries, selects backend (`direct`, `http`, or `auto`), runs search, runs grading, computes metrics, writes outputs, appends history, and prints a console summary.
- `search_utils.py`: Shared constants and helpers (score range, useful threshold, query-score weights, result normalization, grader JSON extraction, failure-reason labeling, JSON/JSONL read-write utilities).
- `reporting.py`: Report builders. Produces `report.md` and HTML content using the template file.
- `report_template.html`: HTML layout/styling scaffold used by `reporting.generate_html_report`.
- `search_queries.json`: Input dataset of queries, categories, expected topics, and intent descriptions.
- `results.json`: Raw normalized search output from the latest run.
- `graded_results.json`: Per-query grader scores and derived fields (`success@k`, `query_score`, failure reasons).
- `history.jsonl`: Append-only run history for trend tracking.

## Run flow

1. Load queries from `search_queries.json`.
2. Execute each query through selected backend and store raw results (`results.json`).
3. Grade top results with LLM grader (`LLMGrader`) and store graded rows (`graded_results.json`).
4. Compute aggregate metrics (avg relevance, success@1/3/5, catastrophic failure rate, per-category breakdown, worst queries).
5. Append run snapshot to `history.jsonl`, then generate `report.md` (and optionally `report.html`).

## Where to tune behavior

- **Scoring weights / thresholds:** edit constants in `search_utils.py` (`QUERY_SCORE_WEIGHT_TOP_1`, `QUERY_SCORE_WEIGHT_SUCCESS_AT_3`, `QUERY_SCORE_WEIGHT_BEST_TOP_5`, `USEFUL_SCORE_THRESHOLD`, `SCORE_MAX`).
- **Search backends / routing:** edit backend classes and selection logic in `run_search_eval.py` (`DirectBackend`, `HTTPBackend`, `choose_backend`), and/or switch backend with `--backend` or `SEARCH_EVAL_BACKEND`.
- **Report template:** edit `report_template.html` for HTML layout/style; edit `reporting.py` for markdown/HTML content structure and metric sections.

Quick run:

```bash
python tests/search_eval/run_search_eval.py
```
