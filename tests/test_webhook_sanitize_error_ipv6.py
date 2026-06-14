"""sanitize_error must scrub IPv6 addresses, not just IPv4.

Webhook delivery errors are stored in Webhook.last_error and surfaced in the
UI. The scrubber removed IPv4 literals but let IPv6 addresses through, so a
failed delivery to an internal v6 host (::1, fe80::/fc00:: ...) leaked the
address. This pins the v6 redaction while keeping the false-positive guards
(clock times, MACs, C++ "::") that make the pattern safe on arbitrary text.
"""

import os
import sys
from unittest.mock import patch

from tests.helpers.import_state import clear_module, preserve_import_state

# Same import dance as test_webhook_ssrf_resilience.py: webhook_manager pulls in
# core.database (init_db -> create_all), which needs a DB path at import time.
# Pin DATABASE_URL to in-memory SQLite and restore module state afterwards.
# sanitize_error itself is pure (stdlib re only).
with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}), \
        preserve_import_state("src.database", "core.database"):
    clear_module("src.database")
    _core_database = sys.modules.get("core.database")
    if _core_database is not None and not getattr(_core_database, "__file__", None):
        del sys.modules["core.database"]
    from src.webhook_manager import sanitize_error


def test_ipv6_addresses_are_redacted():
    leaky = [
        "connect to [fd00::1234:5678]:8080 failed",   # bracketed + port
        "ConnectError to fe80::1 refused",            # link-local
        "no route to ::1",                            # loopback
        "host fc00::abcd unreachable",                # unique-local
        "connect to [::1]:443 refused",               # bracketed + port
        "POST https://[2001:db8::1]:443/hook failed",  # inside a URL
        "addr 2001:0db8:0000:0000:0000:ff00:0042:8329",  # full 8-group
    ]
    for msg in leaky:
        out = sanitize_error(msg)
        # Scrubbed via the v6 rule ([redacted]) or, inside a URL, the URL rule
        # ([redacted-url]) — either way the address must not survive.
        assert "[redacted" in out, out
        assert "::" not in out and "[fd00" not in out, out


def test_non_addresses_are_preserved():
    # Colon-bearing strings that are NOT IPv6 must pass through untouched, so
    # error messages stay readable.
    safe = [
        "failed at 12:34:56 today",                 # clock time
        "2026-06-05T22:36:55 connection reset",     # ISO timestamp
        "std::vector<int> overflow",                # C++ scope resolution
        "device ab:cd:ef:01:23:45 offline",         # MAC address
        "unsupported ratio 16:9",
        "HTTP 500 from upstream",
        "request [deadbeef] failed",                # bracketed hex id, no colon
    ]
    for msg in safe:
        assert sanitize_error(msg) == msg, msg


def test_ipv4_still_redacted_and_length_capped():
    assert sanitize_error("dial 192.168.1.5:9000 refused") == "dial [redacted] refused"
    assert len(sanitize_error("x" * 500)) == 200


def test_ipv6_zone_id_is_redacted():
    # Link-local addresses often carry a %zone (fe80::1%eth0). The whole token,
    # zone included, must go — ipaddress validates the address part.
    out = sanitize_error("bind fe80::1%eth0 unreachable")
    assert "[redacted]" in out
    assert "::" not in out and "%eth0" not in out and "fe80" not in out


def test_ipv4_mapped_ipv6_is_scrubbed():
    # ::ffff:192.168.0.1 must be redacted as a single unit (one [redacted]), not
    # split into "[redacted][redacted]" by the v6 and v4 passes.
    assert sanitize_error("to ::ffff:192.168.0.1 closed") == "to [redacted] closed"


def test_bracketed_scoped_ipv6_with_port_is_one_redaction():
    # [fe80::1%eth0]:8080 — the whole bracketed authority (zone + port) goes,
    # with no leftover brackets/port and no nested [redacted].
    assert sanitize_error("dial [fe80::1%eth0]:8080 timeout") == "dial [redacted] timeout"


def test_bracketed_ipv4_mapped_with_port_is_one_redaction():
    # [::ffff:192.168.0.1]:8080 — same, for an IPv4-mapped literal in brackets.
    assert sanitize_error("dial [::ffff:192.168.0.1]:8080 timeout") == "dial [redacted] timeout"


def test_invalid_ipv6_is_not_partially_mangled():
    # Nine groups is not a valid address. Backing the scrub with ipaddress means
    # the whole token is preserved, instead of a hand-rolled 8-group regex
    # chewing off "1:2:3:4:5:6:7:8" and leaving a dangling ":9".
    msg = "weird id 1:2:3:4:5:6:7:8:9 here"
    assert sanitize_error(msg) == msg
