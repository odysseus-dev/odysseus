from types import SimpleNamespace
from unittest.mock import MagicMock

from src.chat_processor import ChatProcessor


def _job(text):
    def fake_job(*args, **kwargs):
        if isinstance(text, Exception):
            raise text
        return SimpleNamespace(output={"text": text})

    return fake_job


def test_build_context_preface_web_search_success(monkeypatch):
    """Test that LLM correctly extracts and uses a web search query."""
    monkeypatch.setattr("src.chat_processor.submit_model_job", _job("extracted query"), raising=False)

    mock_web_search = MagicMock(return_value=("Search Results", [{"url": "http://mock.com"}]))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)

    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={}, owner="alice")

    processor.build_context_preface(
        message="Some text.\n\nSearch for LLMs.",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
    )

    mock_web_search.assert_called_with("extracted query", time_filter=None, return_sources=True)


def test_build_context_preface_web_search_fallback_on_llm_failure(monkeypatch):
    """Test fallback to original query if LLM fails."""
    monkeypatch.setattr(
        "src.chat_processor.submit_model_job",
        _job(ValueError("LLM down")),
        raising=False,
    )

    mock_web_search = MagicMock(return_value=("Search Results", []))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)

    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="First line\nSecond line",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
    )

    mock_web_search.assert_called_with("First line", time_filter=None, return_sources=True)


def test_build_context_preface_web_search_fallback_on_empty_generation(monkeypatch):
    """Test fallback to original query if LLM returns empty string."""
    monkeypatch.setattr("src.chat_processor.submit_model_job", _job("   \n  "), raising=False)

    mock_web_search = MagicMock(return_value=("Search Results", []))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)

    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="\n\nFallback line\nNext",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
    )

    mock_web_search.assert_called_with("Fallback line", time_filter=None, return_sources=True)


def test_build_context_preface_web_search_query_sanitization(monkeypatch):
    """Test that query is truncated and whitespace collapsed."""
    long_query = "word  " * 50
    monkeypatch.setattr("src.chat_processor.submit_model_job", _job(long_query), raising=False)

    mock_web_search = MagicMock(return_value=("Search Results", []))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)

    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="Message",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
    )

    called_query = mock_web_search.call_args[0][0]
    assert len(called_query) <= 150
    assert "  " not in called_query
