"""Regression tests for issue #5666 — RAG sources bibliography.

Covers two behaviours the chat sources block gained:
  1. Converted Office files are cited by their original name (``Deck.pptx``),
     not the internal ``Deck.pptx.md`` markitdown conversion name.
  2. ``project`` / ``org`` provenance tags flow into both the ``rag_sources``
     list (rendered as chips) and the injected retrieval context — and are
     omitted, byte-for-byte as before, when a document carries no such tags.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.chat_processor import ChatProcessor
from src.markitdown_runtime import original_filename


def test_original_filename_strips_converted_office_suffix():
    assert original_filename("Deck.pptx.md") == "Deck.pptx"
    assert original_filename("Report.docx.md") == "Report.docx"
    assert original_filename("Sheet.XLSX.md") == "Sheet.XLSX"  # case-insensitive


def test_original_filename_leaves_plain_markdown_and_others_untouched():
    # A hand-authored markdown file's stem has no converted extension.
    assert original_filename("notes.md") == "notes.md"
    assert original_filename("README.md") == "README.md"
    # Non-.md files and non-str input pass through unchanged.
    assert original_filename("photo.png") == "photo.png"
    assert original_filename(None) is None


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


def test_rag_sources_surface_original_name_and_provenance_tags():
    hits = [{
        "document": "Q3 roadmap: ship the ingest pipeline.",
        "metadata": {
            "filename": "Atlas-Q3-Product-Roadmap.pptx.md",
            "project": "AI Platform",
            "org": "techinnovators",
        },
        "similarity": 0.82,
    }]
    preface, rag_sources, _web = _preface_for(hits)

    assert len(rag_sources) == 1
    src = rag_sources[0]
    assert src["filename"] == "Atlas-Q3-Product-Roadmap.pptx"  # .md stripped
    assert src["project"] == "AI Platform"
    assert src["org"] == "techinnovators"

    # Provenance is woven into the injected retrieval context so the model
    # can attribute the snippet.
    injected = "\n".join(m["content"] for m in preface)
    assert "Atlas-Q3-Product-Roadmap.pptx (project: AI Platform, org: techinnovators)" in injected


def test_rag_sources_without_tags_are_backwards_compatible():
    hits = [{
        "document": "Plain text note body.",
        "metadata": {"filename": "notes.md"},
        "similarity": 0.5,
    }]
    preface, rag_sources, _web = _preface_for(hits)

    src = rag_sources[0]
    assert src["filename"] == "notes.md"  # untouched
    # No provenance keys leak in when the document isn't tagged.
    assert "project" not in src
    assert "org" not in src

    injected = "\n".join(m["content"] for m in preface)
    assert "[notes.md]" in injected  # bare header, exactly as before
    assert "project:" not in injected
