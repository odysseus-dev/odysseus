"""Resolving DuckDuckGo /l/?uddg= redirects must match the host, not a substring.

`_resolve_ddg_redirect` only extracts the embedded `uddg` destination when the
redirect link is actually on DuckDuckGo. The host check used
`"duckduckgo.com" in parsed.hostname`, which also matches look-alike hosts such
as `duckduckgo.com.evil.com` or `notduckduckgo.com` — so a result link on one of
those would be silently rewritten to its embedded `uddg` target. Same
substring-vs-hostname pitfall fixed for provider detection in 54ecfa3.
"""
from src.search.providers import _resolve_ddg_redirect, _is_duckduckgo_host


def test_resolves_genuine_ddg_redirects():
    # protocol-relative DDG redirect
    assert _resolve_ddg_redirect(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"
    ) == "https://example.com"
    # relative href -> resolved against html.duckduckgo.com (a real DDG subdomain)
    assert _resolve_ddg_redirect(
        "/l/?uddg=https%3A%2F%2Fexample.com"
    ) == "https://example.com"


def test_ignores_lookalike_hosts():
    for host in ("duckduckgo.com.evil.com", "notduckduckgo.com"):
        url = f"https://{host}/l/?uddg=https%3A%2F%2Fexample.com"
        # Must be returned unchanged — it is NOT a DuckDuckGo redirect.
        assert _resolve_ddg_redirect(url) == url


def test_host_matcher():
    assert _is_duckduckgo_host("duckduckgo.com")
    assert _is_duckduckgo_host("html.duckduckgo.com")
    assert _is_duckduckgo_host("lite.duckduckgo.com")
    assert not _is_duckduckgo_host("duckduckgo.com.evil.com")
    assert not _is_duckduckgo_host("notduckduckgo.com")
    assert not _is_duckduckgo_host("")
