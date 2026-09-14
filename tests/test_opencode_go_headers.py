"""OpenCode Go routing headers — regression tests.

Go rejects requests without a stable ``x-opencode-session`` header (HTTP 400,
"Request is missing x-opencode-session"). Detection is host + path based:
``https://opencode.ai/zen/go/...`` is Go, ``https://opencode.ai/zen/...`` is
Zen. See https://opencode.ai/docs/go/#where-can-i-use-it and #6281.
"""
from src import llm_core


class TestOpencodeGoDetection:
    def test_go_surface(self):
        assert llm_core._detect_provider("https://opencode.ai/zen/go/v1") == "opencode-go"
        assert llm_core._provider_label("https://opencode.ai/zen/go/v1") == "OpenCode Go"

    def test_zen_surface(self):
        assert llm_core._detect_provider("https://opencode.ai/zen/v1") == "opencode-zen"
        assert llm_core._provider_label("https://opencode.ai/zen/v1") == "OpenCode Zen"

    def test_lookalikes_stay_openai(self):
        assert llm_core._detect_provider("https://opencode.ai/zenith/v1") == "openai"
        assert llm_core._detect_provider("https://opencode.ai.example/v1") == "openai"
        assert llm_core._detect_provider("https://myproxy.internal/opencode.ai/zen/go/v1") == "openai"


class TestOpencodeGoHeaders:
    def test_session_and_user_agent(self):
        h = llm_core._provider_headers("opencode-go", {"Authorization": "Bearer x"}, "sess-123")
        assert h["x-opencode-session"] == "sess-123"
        assert h["User-Agent"] == llm_core.OPENCODE_GO_USER_AGENT

    def test_fallback_session_when_missing(self):
        h = llm_core._provider_headers("opencode-go", {"Authorization": "Bearer x"}, None)
        assert h["x-opencode-session"] == llm_core.OPENCODE_GO_FALLBACK_SESSION

    def test_does_not_clobber_explicit_user_agent(self):
        h = llm_core._provider_headers("opencode-go", {"User-Agent": "custom/2.0"}, "s1")
        assert h["User-Agent"] == "custom/2.0"
        assert h["x-opencode-session"] == "s1"
