"""The Atlas Bases query engine: operators, joins, sections, computed, safety."""

import pytest

from src.atlas_query import run_query


def _notes():
    return [
        {"path": "daily.md", "title": "Daily", "mtime": 200,
         "content": "---\nstatus: open\ntype: meeting\ntags: [project, todo]\ndate: 2020-01-01\n---\n"
                    "# Daily\n## Todo\nthing\n## Notes\nstuff"},
        {"path": "done.md", "title": "Done", "mtime": 100,
         "content": "---\nstatus: closed\ntype: meeting\n---\n# Done\n## Todo\nold"},
        {"path": "idea.md", "title": "Idea", "mtime": 300,
         "content": "# Idea\nplain note about #project work"},
    ]


def test_eq_and_ne():
    r = run_query({"where": {"filters": [{"field": "prop.status", "op": "eq", "value": "open"}]}}, _notes())
    assert [x["file.path"] for x in r["rows"]] == ["daily.md"]
    r = run_query({"where": {"filters": [{"field": "prop.type", "op": "ne", "value": "meeting"}]}}, _notes())
    assert [x["file.path"] for x in r["rows"]] == ["idea.md"]


def test_tags_contains_frontmatter_and_inline():
    r = run_query({"where": {"filters": [{"field": "file.tags", "op": "contains", "value": "project"}]}}, _notes())
    assert sorted(x["file.path"] for x in r["rows"]) == ["daily.md", "idea.md"]


def test_and_or_joins():
    q_and = {"where": {"join": "and", "filters": [
        {"field": "prop.type", "op": "eq", "value": "meeting"},
        {"field": "prop.status", "op": "eq", "value": "open"}]}}
    assert [x["file.path"] for x in run_query(q_and, _notes())["rows"]] == ["daily.md"]

    q_or = {"where": {"join": "or", "filters": [
        {"field": "prop.status", "op": "eq", "value": "closed"},
        {"field": "file.title", "op": "eq", "value": "Idea"}]}}
    assert sorted(x["file.path"] for x in run_query(q_or, _notes())["rows"]) == ["done.md", "idea.md"]


def test_sections_query_user_example():
    """'sections named todo where status is open' → only daily's Todo section."""
    q = {"from": "sections", "where": {"join": "and", "filters": [
        {"field": "section.heading", "op": "eq", "value": "todo"},
        {"field": "prop.status", "op": "eq", "value": "open"}]}}
    rows = run_query(q, _notes())["rows"]
    assert [(r["file.path"], r["section.heading"]) for r in rows] == [("daily.md", "Todo")]


def test_exists_and_empty():
    r = run_query({"where": {"filters": [{"field": "prop.status", "op": "exists"}]}}, _notes())
    assert sorted(x["file.path"] for x in r["rows"]) == ["daily.md", "done.md"]
    r = run_query({"where": {"filters": [{"field": "prop.status", "op": "empty"}]}}, _notes())
    assert [x["file.path"] for x in r["rows"]] == ["idea.md"]


def test_comparison_and_in_operators():
    r = run_query({"where": {"filters": [{"field": "file.mtime", "op": "gt", "value": 150}]}}, _notes())
    assert sorted(x["file.path"] for x in r["rows"]) == ["daily.md", "idea.md"]
    r = run_query({"where": {"filters": [{"field": "prop.status", "op": "in", "value": ["open", "closed"]}]}}, _notes())
    assert sorted(x["file.path"] for x in r["rows"]) == ["daily.md", "done.md"]


def test_regex_with_size_guard():
    r = run_query({"where": {"filters": [{"field": "file.title", "op": "regex", "value": "^Da"}]}}, _notes())
    assert [x["file.path"] for x in r["rows"]] == ["daily.md"]
    huge = "a" * 5000
    r = run_query({"where": {"filters": [{"field": "file.title", "op": "regex", "value": huge}]}}, _notes())
    assert r["rows"] == []          # oversized pattern rejected, no match, no crash


def test_sort_and_limit():
    q = {"sort": [{"field": "file.mtime", "dir": "desc"}], "limit": 2}
    rows = run_query(q, _notes())["rows"]
    assert [x["file.path"] for x in rows] == ["idea.md", "daily.md"]


def test_computed_column_safe_and_useful():
    q = {"where": {"filters": [{"field": "file.title", "op": "eq", "value": "Daily"}]},
         "computed": {"shout": "upper(file.title)", "age": "days_since(prop.date)"}}
    row = run_query(q, _notes())["rows"][0]
    assert row["shout"] == "DAILY"
    assert isinstance(row["age"], int) and row["age"] > 0


def test_computed_rejects_arbitrary_code():
    q = {"computed": {"evil": "__import__('os').system('echo pwned')"}}
    row = run_query(q, _notes())["rows"][0]
    assert row["evil"] is None      # disallowed call → None, never executed


def test_default_columns_include_props():
    cols = run_query({"from": "notes"}, _notes())["columns"]
    assert "file.title" in cols and "prop.status" in cols


def test_unprefixed_field_resolves_to_property():
    """Agents write 'status', not 'prop.status' — resolve it to the property."""
    q = {"where": {"filters": [{"field": "status", "op": "eq", "value": "open"}]}}
    assert [x["file.path"] for x in run_query(q, _notes())["rows"]] == ["daily.md"]


def test_limit_zero_returns_no_rows():
    """limit:0 means zero rows, not 'everything' (falsy-zero must not be clobbered)."""
    assert run_query({"limit": 0}, _notes())["rows"] == []
    # A missing limit still returns all rows.
    assert len(run_query({}, _notes())["rows"]) == 3


def test_unprefixed_field_in_select_column():
    q = {"where": {"filters": [{"field": "file.title", "op": "eq", "value": "Daily"}]},
         "select": ["status"]}
    row = run_query(q, _notes())["rows"][0]
    assert row["status"] == "open"
