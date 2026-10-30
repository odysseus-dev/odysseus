"""Regression tests for issue #5666: project/org provenance in RAG sources.

``project`` / ``org`` metadata tags flow into both the ``rag_sources`` list
(rendered as chips) and the injected retrieval context, are omitted
byte-for-byte as before when a document carries no such tags, and are
normalized to text at the server boundary so a numeric or boolean tag from a
persisted record can never reach ``esc()`` in the renderers.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.chat_processor import ChatProcessor, _provenance_tag


def _processor_with_rag(hits):
    """A ChatProcessor whose rag_manager.search returns ``hits``."""
    pdm = MagicMock()
    pdm.rag_manager.search.return_value = hits
    return ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=pdm)


def _preface_for(hits):
    processor = _processor_with_rag(hits)
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})
    return processor.build_context_preface(
        message="What is the roadmap?",
        session=session,
        use_web=False,
        use_rag=True,
        use_memory=False,
        use_skills=False,
    )


def test_provenance_tag_normalizes_scalars_and_drops_flags():
    assert _provenance_tag("AI Platform") == "AI Platform"
    assert _provenance_tag("  techinnovators  ") == "techinnovators"
    assert _provenance_tag(42) == "42"
    assert _provenance_tag(3.5) == "3.5"
    # Flags and blanks are not labels.
    assert _provenance_tag(True) is None
    assert _provenance_tag(False) is None
    assert _provenance_tag(None) is None
    assert _provenance_tag("") is None
    assert _provenance_tag("   ") is None


def test_rag_sources_surface_provenance_tags():
    hits = [{
        "document": "Q3 roadmap: ship the ingest pipeline.",
        "metadata": {
            "filename": "Atlas-Q3-Product-Roadmap.pptx",
            "project": "AI Platform",
            "org": "techinnovators",
        },
        "similarity": 0.82,
    }]
    preface, rag_sources, _web = _preface_for(hits)

    assert len(rag_sources) == 1
    src = rag_sources[0]
    assert src["filename"] == "Atlas-Q3-Product-Roadmap.pptx"
    assert src["project"] == "AI Platform"
    assert src["org"] == "techinnovators"

    # Provenance is woven into the injected retrieval context so the model
    # can attribute the snippet.
    injected = "\n".join(m["content"] for m in preface)
    assert "Atlas-Q3-Product-Roadmap.pptx (project: AI Platform, org: techinnovators)" in injected


def test_rag_sources_filename_is_shown_as_stored():
    # A literal ``design.docx.md`` is a real filename; it must not be rewritten
    # into ``design.docx`` by guessing at a conversion that never happened.
    hits = [{
        "document": "Design notes.",
        "metadata": {"filename": "design.docx.md"},
        "similarity": 0.7,
    }]
    _preface, rag_sources, _web = _preface_for(hits)
    assert rag_sources[0]["filename"] == "design.docx.md"


def test_rag_sources_normalize_non_string_provenance():
    hits = [
        {
            "document": "Numeric project tag.",
            "metadata": {"filename": "a.md", "project": 42, "org": True},
            "similarity": 0.9,
        },
        {
            "document": "Blank and missing tags.",
            "metadata": {"filename": "b.md", "project": "  ", "org": None},
            "similarity": 0.8,
        },
    ]
    preface, rag_sources, _web = _preface_for(hits)

    first, second = rag_sources
    # A number becomes text; a boolean is not a label and is dropped.
    assert first["project"] == "42"
    assert "org" not in first
    # Blank and missing tags never produce keys.
    assert "project" not in second
    assert "org" not in second

    injected = "\n".join(m["content"] for m in preface)
    assert "[a.md (project: 42)]" in injected
    assert "[b.md]" in injected


def test_rag_sources_without_tags_are_backwards_compatible():
    hits = [{
        "document": "Plain text note body.",
        "metadata": {"filename": "notes.md"},
        "similarity": 0.5,
    }]
    preface, rag_sources, _web = _preface_for(hits)

    src = rag_sources[0]
    assert src["filename"] == "notes.md"
    # No provenance keys leak in when the document isn't tagged.
    assert "project" not in src
    assert "org" not in src

    injected = "\n".join(m["content"] for m in preface)
    assert "[notes.md]" in injected  # bare header, exactly as before
    assert "project:" not in injected
