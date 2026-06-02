"""Issue #1186 — switching model/endpoint mid-chat must not leak the old API key.

Tests the pure resolution logic in src/session_switch.py with the real
build_headers, so the regression (old endpoint's key sent to the new endpoint →
401) is pinned without needing a live DB or HTTP provider.
"""

from collections import namedtuple

from src.endpoint_resolver import build_headers
from src.session_switch import find_session_endpoint, build_switch_headers

Ep = namedtuple("Ep", "id base_url api_key")

GROQ = Ep("groq", "https://api.groq.com/openai/v1", "groq-key")
CEREBRAS = Ep("cerebras", "https://api.cerebras.ai/v1", "cerebras-key")
LOCAL = Ep("local", "http://localhost:8080/v1", "")
ENDPOINTS = [GROQ, CEREBRAS, LOCAL]


def test_find_by_id():
    assert find_session_endpoint(ENDPOINTS, "cerebras", None) is CEREBRAS


def test_find_by_url_exact_and_chat_suffix():
    assert find_session_endpoint(ENDPOINTS, None, "https://api.cerebras.ai/v1") is CEREBRAS
    # session endpoint_url often carries the /chat/completions suffix
    assert find_session_endpoint(
        ENDPOINTS, None, "https://api.groq.com/openai/v1/chat/completions"
    ) is GROQ


def test_find_missing_returns_none():
    assert find_session_endpoint(ENDPOINTS, None, "https://unknown.example/v1") is None
    assert find_session_endpoint(ENDPOINTS, "nope", None) is None


def test_prefix_url_does_not_match_only_exact():
    # A URL that merely shares a prefix with a stored base must NOT match — else
    # the wrong saved API key gets attached. Exact normalized match only.
    prefix_eps = [
        Ep("a", "https://api.example.com/v1", "key-v1"),
        Ep("b", "https://api.example.com/v1beta", "key-v1beta"),
    ]
    # Sub-path of v1 (not exactly v1) → no match, not a wrong-key guess.
    assert find_session_endpoint(prefix_eps, None, "https://api.example.com/v1/extra") is None
    # A shorter shared base → no match either.
    assert find_session_endpoint(prefix_eps, None, "https://api.example.com") is None
    # Exact still matches.
    assert find_session_endpoint(prefix_eps, None, "https://api.example.com/v1beta") is prefix_eps[1]


def test_switch_uses_new_endpoint_key_not_old():
    # The core regression: switch to Cerebras → headers carry the Cerebras key.
    headers = build_switch_headers(CEREBRAS, "https://api.cerebras.ai/v1", build_headers)
    assert headers.get("Authorization") == "Bearer cerebras-key"
    # And never the previous (Groq) key.
    assert "groq-key" not in str(headers)


def test_switch_with_no_match_clears_stale_auth():
    # No stored endpoint for the new url → headers must NOT carry any old key.
    headers = build_switch_headers(None, "https://unknown.example/v1", build_headers)
    assert "Authorization" not in headers


def test_switch_to_keyless_local_endpoint_has_no_auth():
    headers = build_switch_headers(LOCAL, "http://localhost:8080/v1", build_headers)
    assert "Authorization" not in headers
