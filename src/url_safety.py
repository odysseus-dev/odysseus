"""Outbound URL safety checks to prevent SSRF against internal/cloud services.

Odysseus is local-first by default: connecting to a locally hosted embedding
service (Ollama on 11434, LocalAI, vLLM, LM Studio) or a LAN endpoint does not
break the primary use case. What it *always* rejects:
  - a non-HTTP(S) scheme (``file://``, ``gopher://``, ``ftp://`` …), and
  - the link-local range (``169.254.0.0/16`` / ``fe80::/10``), i.e. the cloud
    instance-metadata SSRF credential-exfil vector — nobody serves embeddings
    there, plus multicast / non-loopback reserved / unspecified addresses.

For exposed multi-tenant deployments, set ``EMBEDDING_BLOCK_PRIVATE_IPS=true`` to
additionally reject all private and loopback targets (full SSRF lockdown).

On a DNS64/NAT64 network an IPv4-only host resolves to the RFC 6052 Well-Known
Prefix ``64:ff9b::/96``. Such an address is decoded to the IPv4 destination the
translator will actually contact, and that destination is then judged under the
strict policy — so the prefix reaches public IPv4 but never tunnels to loopback,
private, shared or link-local space.
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

# RFC 6052 §2.1 Well-Known Prefix for IPv4/IPv6 address translation (NAT64).
# An address inside exactly this /96 is not a destination in its own right: the
# low 32 bits carry the IPv4 address the translator will actually contact. On a
# DNS64/NAT64 network every public IPv4-only host resolves this way, so judging
# the outer IPv6 (which CPython reports as ``is_reserved``) would reject the
# whole public internet while telling us nothing about the real target.
#
# RFC 6052 §3.1 allows the Well-Known Prefix to represent *only* globally
# routable IPv4. The embedded destination is therefore always evaluated under
# the strict policy, whatever ``block_private`` the caller passed: the prefix
# must never become a path to loopback, private, shared, link-local, multicast,
# unspecified or otherwise non-global space.
#
# Deliberately exact. Network-specific prefixes carry locally assigned meaning
# and are NOT decoded here — notably 64:ff9b:1::/48 (RFC 8215), which this /96
# membership test excludes and which stays rejected as reserved.
_NAT64_WELL_KNOWN_PREFIX_V6 = ipaddress.ip_network("64:ff9b::/96")

_LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})

# Well-known cloud instance metadata endpoints that do not sit in link-local (e.g. Alibaba Cloud 100.100.100.200).
_METADATA_IPS = {ipaddress.ip_address("100.100.100.200")}


def _nat64_well_known_embedded_ipv4(
    ip: ipaddress._BaseAddress,
) -> Optional[ipaddress.IPv4Address]:
    """Return the IPv4 target embedded in an RFC 6052 Well-Known-Prefix address.

    ``None`` when ``ip`` is not inside ``64:ff9b::/96``, i.e. when no IPv4
    destination may be inferred from it.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip not in _NAT64_WELL_KNOWN_PREFIX_V6:
        return None
    return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)


def _default_resolver(host: str) -> List[str]:
    """Resolve a hostname to the list of IP strings it maps to (A + AAAA)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _classify(ip: ipaddress._BaseAddress, *, block_private: bool) -> Optional[str]:
    """Return a rejection reason for an IP, or None if it is allowed."""
    # IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) — judge the embedded v4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    else:
        # RFC 6052 Well-Known Prefix — judge the IPv4 destination the NAT64
        # translator will contact, always under the strict policy.
        translated = _nat64_well_known_embedded_ipv4(ip)
        if translated is not None:
            reason = _classify(translated, block_private=True)
            if reason:
                return f"NAT64 translated destination blocked: {reason}"
            return None
    if ip.is_link_local or ip in _METADATA_IPS:
        return f"link-local address blocked (SSRF metadata risk): {ip}"
    # IPv6 loopback is also reserved; its policy must match IPv4 loopback.
    if ip.is_loopback:
        if block_private:
            return f"private/shared/loopback address blocked: {ip}"
        return None
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return f"disallowed address: {ip}"
    if block_private and (
        ip.is_private
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


class OutboundAddressBlocked(PermissionError):
    """A non-HTTP outbound host resolves into a disallowed address range."""


# Alias for backward-compatibility with tests and callers expecting SSRFBlockedError
SSRFBlockedError = OutboundAddressBlocked


def connect_outbound_tcp(
    host: str,
    port: int,
    *,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    block_private: bool = False,
    source_address=None,
    resolver: Optional[Callable[..., list]] = None,
) -> socket.socket:
    """Open a TCP connection to *host* under the outbound address policy.

    For raw TCP clients (IMAP/SMTP). The host is resolved exactly once, every
    resolved address is judged with the same policy as check_outbound_url, and
    the socket connects only to those already-judged addresses. A second
    lookup at connect time (what socket.create_connection(host) does) would let
    a rebinding name pass the check with a public answer and then connect to
    metadata/private space. TLS callers keep wrapping the returned socket with
    ``server_hostname=host``, so SNI and certificate checks are unchanged.
    """
    if not isinstance(host, str):
        raise ValueError("host must be a string")
    host = host.strip()
    if not host:
        raise ValueError("host is required")
    if any(ch.isspace() for ch in host) or any(ch in host for ch in "/\\@?#"):
        raise ValueError("host must be a bare hostname or IP address")

    clean_host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    port = int(port or 0)

    # If clean_host is an IP literal and no custom resolver is injected,
    # evaluate directly and connect.
    ip = None
    if ":" in clean_host:
        try:
            ip = ipaddress.ip_address(clean_host.split("%")[0])
        except ValueError:
            pass
    else:
        try:
            ip = ipaddress.ip_address(clean_host)
        except ValueError:
            pass

    if ip is not None and resolver is None:
        reason = _classify(ip, block_private=block_private)
        if reason:
            raise OutboundAddressBlocked(reason)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect((str(ip), port))
            return sock
        except Exception:
            sock.close()
            raise

    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(clean_host, port, 0, socket.SOCK_STREAM)
    except TypeError:
        # Resolver expects single host argument returning list of IP strings
        raw_ips = resolve(clean_host)
        infos = []
        for raw in raw_ips or []:
            try:
                raw_ip = ipaddress.ip_address(str(raw).split("%")[0])
                af = socket.AF_INET6 if raw_ip.version == 6 else socket.AF_INET
                infos.append((af, socket.SOCK_STREAM, 6, "", (str(raw_ip), port)))
            except ValueError:
                continue

    if infos and isinstance(infos[0], str):
        raw_ips = infos
        infos = []
        for raw in raw_ips:
            try:
                raw_ip = ipaddress.ip_address(str(raw).split("%")[0])
                af = socket.AF_INET6 if raw_ip.version == 6 else socket.AF_INET
                infos.append((af, socket.SOCK_STREAM, 6, "", (str(raw_ip), port)))
            except ValueError:
                continue

    if not infos:
        raise socket.gaierror(socket.EAI_NONAME, f"{host} did not resolve to an address")

    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_addr = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        reason = _classify(ip_addr, block_private=block_private)
        if reason:
            raise OutboundAddressBlocked(reason)

    last_error: Optional[OSError] = None
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise socket.gaierror(socket.EAI_NONAME, f"{host} did not resolve to an address")


def safe_create_connection(
    address: Tuple[str, int],
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: Optional[Tuple[str, int]] = None,
    *,
    block_private: bool = True,
    resolver: Optional[Callable] = None,
) -> socket.socket:
    """Establish a TCP connection guaranteed not to connect to prohibited IP addresses.

    Guards against DNS rebinding (TOCTOU) by resolving the hostname once, validating
    EVERY returned address against the SSRF policy, and connecting directly to the
    validated IP address. The caller retains the original hostname for TLS SNI and
    certificate verification.
    """
    host, port = address
    return connect_outbound_tcp(
        host,
        port,
        timeout=timeout,
        block_private=block_private,
        source_address=source_address,
        resolver=resolver,
    )
