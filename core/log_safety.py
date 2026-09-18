"""Helpers for keeping sensitive data out of logs.

Endpoint URLs configured by admins can embed credentials in the userinfo
(``https://user:pass@host``) or query string (``?api_key=...``). Logging them
raw leaks those secrets, so route/diagnostic logs run URLs through
``redact_url`` first. Reconstructing the URL without userinfo/query/fragment
also doubles as a sanitizer barrier for CodeQL's clear-text-logging query.
"""

from urllib.parse import urlparse, urlunparse


def redact_url(url: str) -> str:
    """Return a URL safe for logs by removing userinfo and query/fragment.

    Keeps scheme, host, port and path so logs stay useful for debugging.
    """
    try:
        raw = url or ""
        parsed = urlparse(raw)
        scheme = parsed.scheme
        reparsed_schemeless = False
        if not parsed.netloc and "@" in raw:
            # A scheme-less ``user:pass@host/path`` parses with the username as
            # the *scheme* and ``pass@host/path`` as the path, so ``hostname``
            # is None and the credentials would survive the rebuild below.
            # Re-parse it as a network-path reference (``//user:pass@host``)
            # so the userinfo lands in netloc, where it is dropped like any
            # other URL's. Admin-entered endpoint URLs commonly omit the
            # scheme, so this is a real input shape, not a corner case.
            parsed = urlparse("//" + raw)
            scheme = ""
            reparsed_schemeless = True
        host = parsed.hostname or ""
        if ":" in host:  # IPv6 literal — re-bracket so host:port stays unambiguous
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        redacted = urlunparse((scheme, host, parsed.path, "", "", ""))
        if reparsed_schemeless and redacted.startswith("//"):
            # Return the URL in the shape it was given (no scheme, no leading
            # ``//``) so log lines stay comparable with the configured value.
            redacted = redacted[2:]
        return redacted
    except Exception:
        return "<endpoint>"
