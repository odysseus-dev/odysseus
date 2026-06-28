"""Provider detection tests — hostname matching helpers (re: #768).

These import the *real* helpers from ``src.llm_core`` (not local copies) so a
regression in hostname matching is actually caught. The point of the change
under test is that provider detection keys off the URL's *hostname*, not a
substring of the whole URL — so a domain appearing in a path/query, or a
look-alike host, must not be misclassified.
"""
from src import llm_core


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
