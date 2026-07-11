"""Regression tests for #4547 — chat-mode web search query sanitization.

Chat-mode web search (``use_web``) selects a search query via the
generated-query flow added in #4557: an LLM extracts a concise query, falling
back to the first non-empty line of the user message when the LLM fails or
returns an empty result. PR #4863 layers a focused, *defensive* cleanup on top
of that flow: whatever query is finally selected (generated or fallback) is
passed through ``_clean_search_query()`` before reaching
``comprehensive_web_search()``, so residual fenced/inline markdown never leaks
into the search call.

``_clean_search_query()`` renders the query to HTML via ``markdown``
(``fenced_code`` extension), drops ``<pre>`` blocks entirely, unwraps inline
``<code>`` to its text (so ``git reset`` survives), collapses whitespace, and
truncates.

The first four tests pin the helper directly; the last three prove it is
wired into the production path and that the combined generated-query +
sanitization behaviour holds for all three selection outcomes (generated
success, LLM exception, empty LLM result).

This is intentionally a narrow interim/defensive fix for #4547; it does not
replace the generated-query flow from #4557.
"""
from src.chat_processor import ChatProcessor, _clean_search_query


# ── Unit tests: _clean_search_query ──


def test_clean_search_query_removes_fenced_code_blocks():
    """A fenced code block must be dropped entirely, including the code body
    and the fences — only the surrounding prose survives."""
    message = '```python\nprint("hello")\n```\nWhat is the capital of France?'

    result = _clean_search_query(message)

    assert result == "What is the capital of France?"
    # Guards against the original leak: no fences, no code body.
    assert "```" not in result
    assert "print" not in result


def test_clean_search_query_preserves_inline_code():
    """Inline code text is search-relevant and must survive unwrapped; only the
    backticks are removed. This is the ``git reset`` case the reviewer flagged
    against the earlier regex approach (which dropped the word entirely)."""
    message = "Is it a good idea to use `git reset` to undo my changes?"

    result = _clean_search_query(message)

    assert result == "Is it a good idea to use git reset to undo my changes?"
    assert "git reset" in result
    assert "`" not in result


def test_clean_search_query_collapses_whitespace():
    """Runs of whitespace (tabs, multiple spaces, newlines) collapse to a single
    space so the query is a single clean line."""
    message = "hello\tworld   foo\n\n   bar"

    result = _clean_search_query(message)

    assert result == "hello world foo bar"
    assert "  " not in result
    assert "\n" not in result
    assert "\t" not in result


def test_clean_search_query_truncates_long_input():
    """Long queries are capped at ``max_len`` (default 200) to stay within search
    API limits; truncation is a strict prefix of the cleaned text."""
    long_message = "x" * 300

    result_default = _clean_search_query(long_message)
    result_custom = _clean_search_query(long_message, max_len=10)

    assert len(result_default) == 200
    assert result_default == "x" * 200
    assert result_custom == "x" * 10


# ── Integration tests: the generated-query + sanitization flow ──
#
# These cover the combined behaviour requested in review of #4863 after #4557
# landed: the LLM-generated query is used on success, the first-line fallback is
# used when the LLM fails or returns empty, and in every case the *final* query
# handed to comprehensive_web_search() is sanitized.

# A messy user message whose first non-empty line (the #4557 fallback) is
# inline-code prose followed by a fenced block. After sanitization the fallback
# collapses to plain prose.
_MESSY = 'Is `git reset` safe?\n```python\nprint("leaked body")\n```'
_SANITIZED_FALLBACK = "Is git reset safe?"


class _Session:
    """Minimal stand-in for the session object read by the generated-query
    flow (endpoint_url / model / headers)."""

    endpoint_url = "http://example.local/v1"
    model = "test-model"
    headers = {"Authorization": "Bearer test"}


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None


def _patch_flow(monkeypatch, llm_behaviour, captured):
    """Wire both seams of the generated-query flow: the LLM call and the
    search call. ``llm_behaviour`` is either a string to return or an Exception
    instance to raise."""

    def _fake_search(query, *args, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return ("web context", [{"title": "src"}])

    def _fake_llm(*args, **kwargs):
        if isinstance(llm_behaviour, Exception):
            raise llm_behaviour
        return llm_behaviour

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", _fake_search)
    monkeypatch.setattr("src.llm_core.llm_call", _fake_llm)


def test_generated_query_is_used_and_sanitized(monkeypatch):
    """Requirement: on LLM success the generated query wins, and the *final*
    query handed to comprehensive_web_search() is sanitized.

    The fake LLM returns a query containing inline-code markdown so we can also
    prove the sanitizer runs on the generated path (not just the fallback)."""
    captured = {}
    _patch_flow(monkeypatch, "capital of `France`", captured)

    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    preface, _, _ = processor.build_context_preface(
        message=_MESSY,
        session=_Session(),
        use_web=True,
        use_memory=False,
        use_rag=False,
    )

    assert "query" in captured, "comprehensive_web_search was not called"

    # The generated query won (not the sanitized first-line fallback) ...
    assert captured["query"] == "capital of France"
    assert captured["query"] != _SANITIZED_FALLBACK
    # ... and it was sanitized: no residual markdown fences/backticks.
    assert "`" not in captured["query"]
    assert "```" not in captured["query"]

    # The other call-site kwargs (return_sources) are still forwarded.
    assert captured["kwargs"].get("return_sources") is True
    # And the retrieved context was still appended to the preface.
    assert any("web context" in (msg.get("content") or "") for msg in preface)


def test_falls_back_to_sanitized_first_line_when_llm_raises(monkeypatch):
    """Requirement: when the LLM call raises, #4557's fallback (first non-empty
    line) is used — and that fallback is sanitized before the search call."""
    captured = {}
    _patch_flow(monkeypatch, RuntimeError("LLM endpoint down"), captured)

    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    processor.build_context_preface(
        message=_MESSY,
        session=_Session(),
        use_web=True,
        use_memory=False,
        use_rag=False,
    )

    assert "query" in captured, "comprehensive_web_search was not called"
    # Fallback was the first line ("Is `git reset` safe?"), sanitized.
    assert captured["query"] == _SANITIZED_FALLBACK
    assert "git reset" in captured["query"]  # inline code preserved
    assert "`" not in captured["query"]  # backticks stripped
    # The fenced body from later lines never reached the query.
    assert "leaked body" not in captured["query"]


def test_falls_back_to_sanitized_first_line_when_llm_returns_empty(monkeypatch):
    """Requirement: when the LLM returns an empty/whitespace-only query, #4557
    falls back — and that fallback is sanitized before the search call."""
    captured = {}
    _patch_flow(monkeypatch, "   ", captured)

    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    processor.build_context_preface(
        message=_MESSY,
        session=_Session(),
        use_web=True,
        use_memory=False,
        use_rag=False,
    )

    assert "query" in captured, "comprehensive_web_search was not called"
    assert captured["query"] == _SANITIZED_FALLBACK
    assert "git reset" in captured["query"]
    assert "`" not in captured["query"]
