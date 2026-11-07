from unittest.mock import MagicMock
import httpx
from types import SimpleNamespace
from src.chat_processor import ChatProcessor

def test_build_context_preface_web_search_success(monkeypatch):
    """Test that LLM correctly extracts and uses a web search query."""
    mock_llm_call = MagicMock(return_value="extracted query")
    monkeypatch.setattr("src.llm_core.llm_call", mock_llm_call)

    mock_web_search = MagicMock(return_value=("Search Results", [{"url": "http://mock.com"}]))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)

    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="Some text.\n\nSearch for LLMs.",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False
    )

    mock_web_search.assert_called_with("extracted query", time_filter=None, return_sources=True)

def test_build_context_preface_web_search_fallback_on_llm_failure(monkeypatch):
    """Test fallback to original query if LLM fails."""
    def failing_llm(*args, **kwargs):
        raise ValueError("LLM down")
    monkeypatch.setattr("src.llm_core.llm_call", failing_llm)

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
        use_skills=False
    )

    mock_web_search.assert_called_with("First line", time_filter=None, return_sources=True)

def test_build_context_preface_web_search_fallback_on_empty_generation(monkeypatch):
    """Test fallback to original query if LLM returns empty string."""
    mock_llm_call = MagicMock(return_value="   \n  ")
    monkeypatch.setattr("src.llm_core.llm_call", mock_llm_call)

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
        use_skills=False
    )

    mock_web_search.assert_called_with("Fallback line", time_filter=None, return_sources=True)

def test_build_context_preface_web_search_query_sanitization(monkeypatch):
    """Test that query is truncated and whitespace collapsed."""
    long_query = "word  " * 50
    mock_llm_call = MagicMock(return_value=long_query)
    monkeypatch.setattr("src.llm_core.llm_call", mock_llm_call)

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
        use_skills=False
    )

    called_query = mock_web_search.call_args[0][0]
    assert len(called_query) <= 150
    assert "  " not in called_query


def _run_web_preface(monkeypatch, message):
    mock_web_search = MagicMock(return_value=("Search Results", []))
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)
    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(
        endpoint_url="http://127.0.0.1:8081/v1/chat/completions", model="gemma4-26b", headers={},
    )
    processor.build_context_preface(
        message=message,
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
    )
    return mock_web_search


def test_truncated_reasoning_never_becomes_the_web_search_query(monkeypatch):
    """A reasoning model that exhausts max_tokens falls back to the user's line."""
    sent = []

    def fake_post(_url, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", "http://127.0.0.1:8081/v1/chat/completions"),
            json={"choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": '* User message: "Who is Jensen?" * Goal: Extract a query.',
                },
            }]},
        )

    monkeypatch.setattr("src.llm_core.httpx.post", fake_post)
    mock_web_search = _run_web_preface(monkeypatch, "Does he work with Lisa Su?\nThanks")

    mock_web_search.assert_called_with(
        "Does he work with Lisa Su?", time_filter=None, return_sources=True,
    )
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_inline_think_block_never_becomes_the_web_search_query(monkeypatch):
    """Without a reasoning parser, thinking arrives as ordinary content."""
    monkeypatch.setattr(
        "src.llm_core.llm_call",
        MagicMock(return_value="<think>The user is asking about"),
    )
    mock_web_search = _run_web_preface(monkeypatch, "Who is Jensen Huang?")

    mock_web_search.assert_called_with("Who is Jensen Huang?", time_filter=None, return_sources=True)


def _turn(role, content):
    return SimpleNamespace(role=role, content=content)


def _extraction_messages(monkeypatch, message, history, **kwargs):
    mock_llm_call = MagicMock(return_value="query")
    monkeypatch.setattr("src.llm_core.llm_call", mock_llm_call)
    monkeypatch.setattr(
        "src.chat_processor.comprehensive_web_search",
        MagicMock(return_value=("Search Results", [])),
    )
    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    processor.build_context_preface(
        message=message,
        session=SimpleNamespace(endpoint_url="http://local", model="test", headers={}, history=history),
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
        **kwargs,
    )
    return mock_llm_call.call_args.args[2]


def test_follow_up_query_extraction_sees_recent_turns(monkeypatch):
    followup = "Does he work with Lisa Su?"
    messages = _extraction_messages(monkeypatch, followup, [
        _turn("user", "Who is Jensen Huang from NVIDIA?"),
        _turn("assistant", "<think>Let me look.</think>Jensen Huang leads NVIDIA."),
        _turn("user", followup),
    ])

    assert messages[1]["content"] == (
        "Earlier conversation:\n"
        "user: Who is Jensen Huang from NVIDIA?\n"
        "assistant: Jensen Huang leads NVIDIA.\n\n"
        f"Latest message:\n{followup}"
    )


def test_first_message_query_extraction_is_only_the_message(monkeypatch):
    messages = _extraction_messages(monkeypatch, "Who is Jensen?", [_turn("user", "Who is Jensen?")])

    assert messages[1] == {"role": "user", "content": "Who is Jensen?"}


def test_query_extraction_history_is_bounded(monkeypatch):
    history = [_turn("user", f"old-{i} " + "x" * 2000) for i in range(10)]
    history.append(_turn("assistant", "reply"))
    prompt = _extraction_messages(monkeypatch, "next", history)[1]["content"]

    assert "old-6" not in prompt
    assert "old-7" in prompt and "old-9" in prompt
    assert len(prompt) < 2200


def test_incognito_query_extraction_ignores_saved_history(monkeypatch):
    messages = _extraction_messages(
        monkeypatch, "Does he code?", [_turn("user", "Saved chat"), _turn("assistant", "Reply")],
        incognito=True,
    )

    assert messages[1] == {"role": "user", "content": "Does he code?"}
