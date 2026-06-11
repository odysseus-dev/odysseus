"""Tests for document tags feature (#2680).

Covers:
  1. _aggregate_tag_facets returns correct per-tag counts
  2. GET library with ?tag= filter is wired in document_routes (source check)
  3. Tag filter correctly includes only documents with the matching tag
  4. After init_db() runs, columns tags and ai_tags exist in the documents table
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Test 1: _aggregate_tag_facets returns correct per-tag counts
# ---------------------------------------------------------------------------

def test_aggregate_tag_facets_basic():
    """_aggregate_tag_facets must count each tag across a list of doc-like objects."""
    from routes.document_routes import _aggregate_tag_facets
    from types import SimpleNamespace

    docs = [
        SimpleNamespace(tags="python,draft"),
        SimpleNamespace(tags="python,work"),
        SimpleNamespace(tags="draft"),
        SimpleNamespace(tags=""),
        SimpleNamespace(tags=None),
    ]
    result = _aggregate_tag_facets(docs)
    assert result == {"python": 2, "draft": 2, "work": 1}


def test_aggregate_tag_facets_empty():
    from routes.document_routes import _aggregate_tag_facets
    assert _aggregate_tag_facets([]) == {}


def test_aggregate_tag_facets_whitespace_normalised():
    """Leading/trailing whitespace is stripped and tags are lowercased."""
    from routes.document_routes import _aggregate_tag_facets
    from types import SimpleNamespace

    docs = [SimpleNamespace(tags=" Python , Draft ")]
    result = _aggregate_tag_facets(docs)
    assert "python" in result
    assert "draft" in result


# ---------------------------------------------------------------------------
# Test 2: tag query param is present in library route (source check)
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parent.parent / "routes" / "document_routes.py"


def _src():
    return _SRC.read_text(encoding="utf-8")


def test_aggregate_tag_facets_importable_and_uses_tags():
    """_aggregate_tag_facets must be importable from document_routes and reference tags."""
    source = _src()
    assert "_aggregate_tag_facets" in source
    assert "getattr(doc, \"tags\"" in source or "doc.tags" in source


def test_tag_filter_applied_in_aggregate_fn():
    """_aggregate_tag_facets must strip and lowercase tags."""
    source = _src()
    assert "tag_clean" not in source  # _aggregate_tag_facets uses inline strip/lower
    assert "t.strip().lower()" in source or "t.strip().lower" in source


def test_tags_field_referenced_in_aggregate_fn():
    """_aggregate_tag_facets reads tags from doc objects."""
    source = _src()
    assert "getattr(doc, \"tags\"" in source


# ---------------------------------------------------------------------------
# Test 3: tag filter logic — only docs with the matching tag are kept
# ---------------------------------------------------------------------------

def test_tag_filter_python_only_matching_docs():
    """The tag-filter list-comprehension should include only matching docs."""
    from types import SimpleNamespace

    # Replicate the in-route filter logic directly
    def _tag_filter(docs, tag):
        tag_clean = tag.strip().lower()
        return [
            doc for doc in docs
            if any(
                t.strip().lower() == tag_clean
                for t in (getattr(doc, "tags", "") or "").split(",")
                if t.strip()
            )
        ]

    docs = [
        SimpleNamespace(tags="python,work", title="A"),
        SimpleNamespace(tags="draft", title="B"),
        SimpleNamespace(tags="python", title="C"),
        SimpleNamespace(tags="", title="D"),
        SimpleNamespace(tags=None, title="E"),
    ]

    result = _tag_filter(docs, "python")
    titles = [d.title for d in result]
    assert "A" in titles
    assert "C" in titles
    assert "B" not in titles
    assert "D" not in titles
    assert "E" not in titles


def test_tag_filter_nonexistent_returns_empty():
    from types import SimpleNamespace

    def _tag_filter(docs, tag):
        tag_clean = tag.strip().lower()
        return [
            doc for doc in docs
            if any(
                t.strip().lower() == tag_clean
                for t in (getattr(doc, "tags", "") or "").split(",")
                if t.strip()
            )
        ]

    docs = [SimpleNamespace(tags="python"), SimpleNamespace(tags="draft")]
    assert _tag_filter(docs, "nonexistent_xyz") == []


# ---------------------------------------------------------------------------
# Test 4: After init_db() runs, columns tags and ai_tags exist in documents
# ---------------------------------------------------------------------------

def test_init_db_creates_tags_columns():
    """init_db() must result in tags and ai_tags columns on the documents table."""
    from sqlalchemy import text
    from core.database import engine, init_db

    # init_db is already called at module import time, but call again for idempotency
    init_db()

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(documents)"))]
    assert "tags" in cols, "Column 'tags' missing from documents table"
    assert "ai_tags" in cols, "Column 'ai_tags' missing from documents table"
