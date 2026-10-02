"""Outbound URL safety checks (SSRF hardening).

Run before the server makes a request to a *user-supplied* URL — e.g. the custom
embedding endpoint set via ``POST /api/embeddings/endpoint``, which then triggers
an outbound ``httpx`` call.

Odysseus is local-first: pointing the embedding endpoint at a loopback or LAN
address (a local vLLM / llama.cpp / Ollama server) is a normal, intended setup.
So this guard does **not** blanket-block private addresses by default — that would
break the primary use case. What it *always* rejects:

  - a non-HTTP(S) scheme (``file://``, ``gopher://``, ``ftp://`` …), and
  - the link-local range (``169.254.0.0/16`` / ``fe80::/10``), i.e. the cloud
    instance-metadata SSRF credential-exfil vector — nobody serves embeddings
    there — plus multicast / reserved / unspecified addresses.

For exposed multi-tenant deployments, set ``EMBEDDING_BLOCK_PRIVATE_IPS=true`` to
additionally reject all private and loopback targets (full SSRF lockdown).
"""

import ipaddress
import socket
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# RFC 6598 shared address space (carrier-grade NAT). It is not globally
# routable, but CPython does not classify it as ``is_private`` (it is "shared",
# not "private"), so the is_private/is_loopback checks miss it. Reject the range
# explicitly. This closes exactly the shared-space gap without coupling strict
# mode to ``is_global``'s broader definition, which has shifted across CPython
# versions for other special ranges.
_SHARED_ADDRESS_SPACE_V4 = ipaddress.ip_network("100.64.0.0/10")


def _default_resolver(host: str) -> List[str]:
    """Resolve a hostname to the list of IP strings it maps to (A + AAAA)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _classify(ip: ipaddress._BaseAddress, *, block_private: bool) -> Optional[str]:
    """Return a rejection reason for an IP, or None if it is allowed."""
    # IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) — judge the embedded v4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return f"link-local address blocked (SSRF metadata risk): {ip}"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return f"disallowed address: {ip}"
    if block_private and (
        ip.is_private
        or ip.is_loopback
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_ADDRESS_SPACE_V4)
    ):
        return f"private/shared/loopback address blocked: {ip}"
    return None


def check_outbound_url(
    url: str,
    *,
    block_private: bool = False,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> Tuple[bool, str]:
    """Validate a user-supplied outbound URL.

    Returns ``(ok, reason)``. ``ok`` is True only when the URL is safe to fetch.
    ``resolver`` is injectable so callers/tests can avoid real DNS.
    """
    if not isinstance(url, str):
        return False, "URL must be a string"
    if not url or not url.strip():
        return False, "URL is required"
    try:
        parsed = urlparse(url.strip())
    except Exception as e:  # pragma: no cover - urlparse is very tolerant
        return False, f"unparseable URL: {e}"

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme must be http or https, got '{parsed.scheme or '(none)'}'"
    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    resolve = resolver or _default_resolver
    try:
        raw_ips = resolve(host)
    except Exception as e:
        return False, f"host does not resolve: {e}"
    if not raw_ips:
        return False, "host does not resolve"

    saw_ip = False
    for raw in raw_ips:
        if not isinstance(raw, str):
            continue
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])  # strip IPv6 zone id
        except ValueError:
            continue
        saw_ip = True
        reason = _classify(ip, block_private=block_private)
        if reason:
            return False, reason
    if not saw_ip:
        return False, "host does not resolve to an IP"
    return True, "ok"


_LOCALHOST_NAMES = ("localhost", "ip6-localhost", "ip6-loopback")


def _is_localhost_name(host: str) -> bool:
    h = host.lower().rstrip(".")
    return h in _LOCALHOST_NAMES or h.endswith(".localhost")


def check_outbound_host(
    host: str,
    *,
    block_private: bool = False,
    resolver: Optional[Callable[[str], List[str]]] = None,
    unresolved: str = "reject",
) -> Tuple[bool, str]:
    """Validate a bare user-supplied hostname / IP literal (no scheme).

    Companion to :func:`check_outbound_url` for protocols that take a host
    and port rather than a URL — IMAP, SMTP, CalDAV/CardDAV hosts, database
    DSNs. Same policy: link-local / multicast / reserved / unspecified
    addresses are always rejected (cloud metadata SSRF), private / loopback
    only when ``block_private`` is set. ``localhost`` spellings are treated as
    loopback without a DNS round-trip.

    ``unresolved`` controls what happens when a hostname does not resolve:

    - ``"reject"`` (default, fail-closed like ``check_outbound_url``);
    - ``"allow"``: pass it through. Use this only where the very next step
      is a connect that will surface the same DNS failure to the user, so
      an offline host or a flaky resolver does not turn into a spurious
      "blocked" error. IP literals and localhost names are still classified
      without DNS, so the metadata / loopback cases are covered either way.
    """
    if not isinstance(host, str):
        return False, "host must be a string"
    host = host.strip()
    if not host:
        return False, "host is required"
    # A bare host never carries a scheme, path, userinfo, port or whitespace.
    # Anything of the sort is either a copy/paste mistake or an attempt to
    # smuggle a target past the check; reject rather than guess.
    if any(ch.isspace() for ch in host) or any(ch in host for ch in "/\\@?#"):
        return False, "host must be a bare hostname or IP address"
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if ":" in literal:
        # Only an IPv6 literal may contain colons; "host:port" is rejected.
        try:
            ip = ipaddress.ip_address(literal.split("%")[0])
        except ValueError:
            return False, "host must be a bare hostname or IP address"
        reason = _classify(ip, block_private=block_private)
        return (False, reason) if reason else (True, "ok")
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        ip = None
    if ip is not None:
        reason = _classify(ip, block_private=block_private)
        return (False, reason) if reason else (True, "ok")
    if _is_localhost_name(host):
        if block_private:
            return False, f"private/shared/loopback address blocked: {host}"
        return True, "ok"

    resolve = resolver or _default_resolver
    try:
        raw_ips = resolve(host)
    except Exception as e:
        if unresolved == "allow":
            return True, "unresolved (deferred to connect)"
        return False, f"host does not resolve: {e}"
    saw_ip = False
    for raw in raw_ips or []:
        if not isinstance(raw, str):
            continue
        try:
            resolved = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        saw_ip = True
        reason = _classify(resolved, block_private=block_private)
        if reason:
            return False, reason
    if not saw_ip:
        if unresolved == "allow":
            return True, "unresolved (deferred to connect)"
        return False, "host does not resolve to an IP"
    return True, "ok"
