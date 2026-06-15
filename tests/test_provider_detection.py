"""Provider detection tests (re: #768).

These import the *real* helpers from ``src.llm_core`` (not local copies) so a
regression in hostname matching is actually caught. The point of the change
under test is that provider detection keys off the URL's *hostname*, not a
substring of the whole URL — so a domain appearing in a path/query, or a
look-alike host, must not be misclassified.
"""
import pytest

from src import llm_core
from src import endpoint_resolver
from src.endpoint_resolver import build_chat_url, build_models_url


class TestHostMatch:
    def test_exact_host(self):
        assert llm_core._host_match("https://anthropic.com/v1", "anthropic.com")

    def test_subdomain(self):
        assert llm_core._host_match("https://api.anthropic.com/v1", "anthropic.com")

    def test_multiple_domains(self):
        assert llm_core._host_match("https://api.together.ai/v1", "together.xyz", "together.ai")

    def test_trailing_dot_fqdn(self):
        # A fully-qualified host with a trailing dot is legal and resolvable.
        assert llm_core._host_match("https://api.anthropic.com./v1", "anthropic.com")

    def test_domain_in_path_does_not_match(self):
        assert not llm_core._host_match("https://myproxy.internal/anthropic.com/v1", "anthropic.com")

    def test_domain_in_query_does_not_match(self):
        assert not llm_core._host_match("https://example.com/v1?ref=anthropic.com", "anthropic.com")

    def test_lookalike_host_does_not_match(self):
        assert not llm_core._host_match("https://anthropic.com.example/v1", "anthropic.com")

    def test_none_and_empty_safe(self):
        assert not llm_core._host_match(None, "anthropic.com")
        assert not llm_core._host_match("", "anthropic.com")


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


class TestBuildersRejectLookalikeHosts:
    """build_chat_url / build_models_url must route look-alike and
    domain-in-path hosts to the OpenAI-compatible default, not the
    anthropic/ollama branches. Before #815's follow-up these builders still
    fell back to ``host.endswith("anthropic.com")`` style checks, so
    ``notanthropic.com`` was misrouted to the Anthropic messages endpoint.
    """

    @pytest.fixture(autouse=True)
    def _stub_dns(self, monkeypatch):
        # build_* call resolve_url(), which does real DNS + tailscale lookups.
        # Provider routing is independent of name resolution, so stub it out to
        # keep these deterministic and offline.
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda u: u)

    def test_real_anthropic_chat(self):
        assert build_chat_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"

    def test_chatgpt_subscription_chat_uses_responses(self):
        assert build_chat_url("https://chatgpt.com/backend-api/codex") == "https://chatgpt.com/backend-api/codex/responses"

    def test_chatgpt_subscription_models_uses_no_live_probe(self):
        assert build_models_url("https://chatgpt.com/backend-api/codex") is None

    def test_lookalike_anthropic_chat_is_openai(self):
        assert build_chat_url("https://notanthropic.com") == "https://notanthropic.com/chat/completions"

    def test_lookalike_anthropic_models_is_openai(self):
        assert build_models_url("https://anthropic.com.evil.com") == "https://anthropic.com.evil.com/models"

    def test_anthropic_domain_in_path_is_openai(self):
        assert build_chat_url("https://myproxy.internal/anthropic.com/v1") == "https://myproxy.internal/anthropic.com/v1/chat/completions"

    def test_real_ollama_chat(self):
        assert build_chat_url("https://ollama.com") == "https://ollama.com/api/chat"

    def test_lookalike_ollama_chat_is_openai(self):
        assert build_chat_url("https://notollama.com") == "https://notollama.com/chat/completions"

    def test_lookalike_ollama_models_is_openai(self):
        assert build_models_url("https://notollama.com") == "https://notollama.com/models"


class TestBuildersLocalAndDockerEndpoints:
    """Local and docker endpoints must keep working after the hostname change:
    a local ``/v1`` base stays OpenAI-compatible, and a native Ollama ``/api``
    path is still detected by path even on a non-ollama.com host such as
    host.docker.internal.
    """

    @pytest.fixture(autouse=True)
    def _stub_dns(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda u: u)

    def test_local_v1_chat_is_openai_compatible(self):
        assert build_chat_url("http://localhost:8000/v1") == "http://localhost:8000/v1/chat/completions"

    def test_local_v1_models_is_openai_compatible(self):
        assert build_models_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1/models"

    def test_docker_internal_ollama_api_path_is_native_chat(self):
        assert build_chat_url("http://host.docker.internal:11434/api") == "http://host.docker.internal:11434/api/chat"

    def test_docker_internal_ollama_api_path_is_native_models(self):
        assert build_models_url("http://host.docker.internal:11434/api") == "http://host.docker.internal:11434/api/tags"
