"""Provider detection tests — _detect_provider real hosts and false-positive rejection (re: #768).

These import the *real* helpers from ``src.llm_core`` (not local copies) so a
regression in hostname matching is actually caught. The point of the change
under test is that provider detection keys off the URL's *hostname*, not a
substring of the whole URL — so a domain appearing in a path/query, or a
look-alike host, must not be misclassified.
"""
from src import llm_core


class TestDetectProviderRealHosts:
    def test_chatgpt_subscription_codex_backend(self):
        assert llm_core._detect_provider("https://chatgpt.com/backend-api/codex") == "chatgpt-subscription"
        assert llm_core._detect_provider("https://chatgpt.com/backend-api/codex/responses") == "chatgpt-subscription"

    def test_anthropic(self):
        assert llm_core._detect_provider("https://api.anthropic.com") == "anthropic"

    def test_openrouter(self):
        assert llm_core._detect_provider("https://openrouter.ai/api/v1") == "openrouter"

    def test_groq_openai_compat_path(self):
        # Groq's base carries an /openai/v1 path; detection must still see the host.
        assert llm_core._detect_provider("https://api.groq.com/openai/v1") == "groq"

    def test_ollama_native_unchanged(self):
        assert llm_core._detect_provider("https://ollama.com/api") == "ollama"

    def test_unknown_host_defaults_to_openai(self):
        assert llm_core._detect_provider("https://api.example.com/v1") == "openai"


class TestDetectProviderRejectsSubstringFalsePositives:
    """The regression that motivated #768: substring matching mislabeled these."""

    def test_provider_domain_in_path(self):
        assert llm_core._detect_provider("https://myproxy.internal/anthropic.com/v1") == "openai"

    def test_provider_domain_in_query(self):
        assert llm_core._detect_provider("https://example.com/v1?ref=anthropic.com") == "openai"

    def test_lookalike_host(self):
        assert llm_core._detect_provider("https://anthropic.com.example/v1") == "openai"

    def test_none_safe(self):
        assert llm_core._detect_provider(None) == "openai"
