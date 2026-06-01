"""Regression tests for consistent LLM endpoint URL normalization.

Covers the bug where the agent path POSTed a stored endpoint_url as-is (e.g. a
base ending in /v1) with no /chat/completions appended — a silent 404 + empty
model response — while the non-agent path normalized via build_chat_url. Also
covers bare local Ollama hosts (host:port with no /api path) and double-path
guards (/chat/chat, /chat/completions/chat/completions).

The real functions are imported; conftest stubs the heavy deps so the pure URL
helpers run without a live container.
"""
import importlib

from src import llm_core


def _real_endpoint_resolver():
    """Return the REAL endpoint_resolver module.

    Another test module (test_auth_regressions.py) stubs
    `src.endpoint_resolver` as a MagicMock in sys.modules at import time, so a
    plain `from src import endpoint_resolver` would pick up the mock under a
    full-suite run (`pytest tests/`) — and that mock has a None __spec__, so
    importlib.util.find_spec can't recover it. Load the real module straight
    from its on-disk file so `build_chat_url` is the actual implementation
    regardless of import order."""
    import os
    src_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "endpoint_resolver.py",
    )
    spec = importlib.util.spec_from_file_location(
        "src.endpoint_resolver._real_for_url_test", src_file
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


er = _real_endpoint_resolver()


# ── OpenAI-compatible normalization (llm_core) ──

def test_openai_v1_base_gets_chat_completions():
    # The driver-confirmed live bug: .../v1 with nothing appended -> 404.
    assert (
        llm_core._normalize_openai_chat_url("http://localhost:8000/v1")
        == "http://localhost:8000/v1/chat/completions"
    )


def test_openai_full_chat_completions_is_idempotent():
    full = "http://localhost:8000/v1/chat/completions"
    assert llm_core._normalize_openai_chat_url(full) == full
    # Trailing slash tolerated, still no double path.
    assert llm_core._normalize_openai_chat_url(full + "/") == full


def test_openai_bare_host_gets_chat_completions():
    assert (
        llm_core._normalize_openai_chat_url("http://localhost:8000")
        == "http://localhost:8000/chat/completions"
    )


# ── Ollama normalization (llm_core) ──

def test_ollama_bare_local_host_gets_api_chat():
    # U4: bare http://localhost:11434 must resolve to /api/chat, not /chat.
    assert (
        llm_core._normalize_ollama_url("http://localhost:11434")
        == "http://localhost:11434/api/chat"
    )


def test_ollama_api_root_gets_chat():
    assert (
        llm_core._normalize_ollama_url("http://localhost:11434/api")
        == "http://localhost:11434/api/chat"
    )


def test_ollama_cloud_host_gets_api_chat():
    assert (
        llm_core._normalize_ollama_url("https://ollama.com")
        == "https://ollama.com/api/chat"
    )


def test_ollama_no_double_chat_chat():
    # U5: an already-complete /api/chat URL must not become /api/chat/chat.
    full = "http://localhost:11434/api/chat"
    assert llm_core._normalize_ollama_url(full) == full
    assert llm_core._normalize_ollama_url(full + "/") == full


# ── endpoint_resolver.build_chat_url mirrors llm_core ──

def test_build_chat_url_v1_base():
    assert (
        er.build_chat_url("http://localhost:8000/v1")
        == "http://localhost:8000/v1/chat/completions"
    )


def test_build_chat_url_full_is_idempotent():
    full = "http://localhost:8000/v1/chat/completions"
    assert er.build_chat_url(full) == full


def test_build_chat_url_bare_local_ollama():
    assert (
        er.build_chat_url("http://localhost:11434")
        == "http://localhost:11434/api/chat"
    )


def test_build_chat_url_ollama_api_no_double_chat():
    assert er.build_chat_url("http://localhost:11434/api") == "http://localhost:11434/api/chat"
    assert er.build_chat_url("http://localhost:11434/api/chat") == "http://localhost:11434/api/chat"


# ── agent vs non-agent path consistency ──

def test_agent_and_non_agent_paths_agree_on_chat_url():
    """The whole point of the fix: whatever URL shape is stored, the agent path
    (llm_core._normalize_openai_chat_url applied at POST time) and the non-agent
    path (endpoint_resolver.build_chat_url) must land on the same endpoint."""
    for base in (
        "http://localhost:8000/v1",
        "http://localhost:8000/v1/chat/completions",
        "http://localhost:8000",
    ):
        assert llm_core._normalize_openai_chat_url(base) == er.build_chat_url(base)
