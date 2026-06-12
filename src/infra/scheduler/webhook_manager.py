"""Outgoing webhook manager — fires HTTP POSTs when events happen."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from src.database import SessionLocal, Webhook

logger = logging.getLogger(__name__)

ALLOWED_EVENTS = frozenset({
    "session.created",
    "chat.completed",
    "chat.message",
    "webhook.test",
})

# Block requests to private/internal networks
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _utcnow() -> datetime:
    """Return naive UTC for existing DB columns while avoiding datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ip_is_private(addr: ipaddress._BaseAddress) -> bool:
    # If the address is IPv4-mapped IPv6, extract and evaluate the embedded IPv4
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True

    return any(addr in net for net in _PRIVATE_NETWORKS)


def _resolve_hostname_ips(hostname: str) -> list:
    """Resolve a hostname to all its A/AAAA records. Empty list on failure."""
    import socket
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return []
    out = []
    for info in infos:
        sockaddr = info[4]
        try:
            out.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    return out


def _is_private_url(url: str) -> bool:
    """Check if a URL points to a private/internal address.

    Resolves DNS names so attackers can't hide an internal IP behind
    `internal.lan` or `127.0.0.1.nip.io`. Re-checked at delivery time too,
    as a partial defense against DNS rebinding.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip()
        if not hostname:
            return True
        # Block common internal hostnames + suffixes the resolver may not catch.
        h_lower = hostname.lower()
        if h_lower in ("localhost", "0.0.0.0", "metadata.google.internal", "metadata"):
            return True
        if h_lower.endswith((".local", ".internal", ".lan", ".intranet", ".localhost")):
            return True
        # IP literal? short-circuit.
        try:
            return _ip_is_private(ipaddress.ip_address(hostname))
        except ValueError:
            pass
        # DNS hostname — resolve and check every record.
        addrs = _resolve_hostname_ips(hostname)
        if not addrs:
            # Couldn't resolve → fail closed; let validation reject the URL.
            return True
        return any(_ip_is_private(a) for a in addrs)
    except ValueError:
        return True


def validate_webhook_url(url: str) -> str:
    """Validate and normalize a webhook URL. Raises ValueError if invalid."""
    url = url.strip()
    if len(url) > 2048:
        raise ValueError("URL too long (max 2048 characters)")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname")
    if _is_private_url(url):
        raise ValueError("URL must not point to private/internal addresses")
    return url


def validate_events(events_str: str) -> str:
    """Validate comma-separated event names. Returns cleaned string."""
    events = [e.strip() for e in events_str.split(",") if e.strip()]
    if not events:
        raise ValueError("At least one event is required")
    invalid = set(events) - ALLOWED_EVENTS
    if invalid:
        raise ValueError(f"Invalid events: {', '.join(sorted(invalid))}. Allowed: {', '.join(sorted(ALLOWED_EVENTS - {'webhook.test'}))}")
    return ",".join(events)


# Broad candidate matcher for the IP-redaction pass. Deliberately loose: a
# bracketed host authority ([fe80::1%eth0]:8080 and friends) with an optional
# :port, or a bare IPv6 run — hex groups joined by colons, an optional trailing
# dotted-quad for IPv4-mapped forms (::ffff:192.168.0.1), and an optional %zone.
# It does NOT encode the IPv6 grammar; ipaddress.ip_address() is the real
# validator (see _redact_ip_candidate), so any colon-bearing string it rejects
# (clock times, MACs, "std::vector") is left alone. Every branch is a single
# greedy class or a repetition over a mandatory ':'/'.' delimiter, so there is no
# nested-quantifier backtracking (ReDoS-safe).
_IP_CANDIDATE = re.compile(
    r'\[[^\[\]\s]*\](?::\d+)?'
    r'|(?<![\w.:%])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,}'
    r'(?:(?:\.[0-9]{1,3}){3})?(?:%[0-9A-Za-z._-]+)?'
)


def _redact_ip_candidate(match: re.Match) -> str:
    """Redact a candidate token that the stdlib confirms is an IP address.

    A bare token is redacted only when it parses as IPv6 — bare IPv4 is left to
    the dedicated IPv4 pass. A bracketed token is a host authority, so a v4 or v6
    literal inside [ ] is redacted as a whole. This keeps output consistent (one
    [redacted], never nested or partial) for scoped/mapped/ported forms.
    """
    token = match.group(0)
    bracketed = token.startswith('[')
    candidate = token
    if bracketed:
        # Keep only what's inside [...]; the trailing :port is dropped.
        candidate = candidate[1:candidate.index(']')]
    # A zone id (fe80::1%eth0) is not part of the address ipaddress parses.
    candidate = candidate.split('%', 1)[0]
    # The loose bare pattern can trail one stray ':' (e.g. "::1:" in "host ::1:
    # down"); drop it unless it's the "::" compression marker.
    if candidate.endswith(':') and not candidate.endswith('::'):
        candidate = candidate[:-1]
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return token
    if bracketed or isinstance(addr, ipaddress.IPv6Address):
        return '[redacted]'
    return token


def sanitize_error(error: str, max_len: int = 200) -> str:
    """Strip potentially sensitive details from error messages."""
    # Redact IPv6 (and bracketed-authority) addresses first, so an IPv4-mapped
    # form like ::ffff:192.168.0.1 is scrubbed as one unit instead of having its
    # embedded IPv4 removed first and leaving a stray "::ffff:" behind. Broad
    # candidates are validated by ipaddress.ip_address(), so the false-positive
    # guards (clock times, MACs, C++ "::") come from the stdlib, not a regex.
    cleaned = _IP_CANDIDATE.sub(_redact_ip_candidate, error)
    # Remove remaining bare IPv4 addresses and ports.
    cleaned = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?', '[redacted]', cleaned)
    # Remove hostnames in URLs.
    cleaned = re.sub(r'https?://[^\s/]+', '[redacted-url]', cleaned)
    return cleaned[:max_len]


class WebhookManager:
    def __init__(self, api_key_manager=None):
        # Disable redirects to prevent SSRF via redirect chains
        self._client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._api_key_manager = api_key_manager

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _decrypt_secret(self, encrypted: Optional[str]) -> Optional[str]:
        """Decrypt a webhook signing secret from DB storage."""
        if not encrypted:
            return None
        if self._api_key_manager:
            try:
                return self._api_key_manager.decrypt_api_key(encrypted)
            except Exception:
                # If decryption fails, assume it's stored in plaintext (legacy)
                return encrypted
        return encrypted

    def fire_and_forget(self, event: str, payload: dict):
        """Schedule webhook fire from any context (sync or async). Never blocks."""
        if event not in ALLOWED_EVENTS:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.fire(event, payload))
        except RuntimeError:
            # Called from a sync thread (e.g. sync FastAPI route in threadpool)
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.fire(event, payload), self._loop)

    async def fire(self, event: str, payload: dict):
        """Fire webhooks matching the given event."""
        if event not in ALLOWED_EVENTS:
            return
        db = SessionLocal()
        try:
            webhooks = db.query(Webhook).filter(Webhook.is_active == True).all()
            matching = [w for w in webhooks if event in w.events.split(",")]
        finally:
            db.close()

        for wh in matching:
            decrypted_secret = self._decrypt_secret(wh.secret)
            asyncio.create_task(self._deliver(wh.id, wh.url, decrypted_secret, event, payload))

    async def deliver_test(self, webhook_id: str, url: str, encrypted_secret: Optional[str]):
        """Public method for the test-webhook route."""
        decrypted = self._decrypt_secret(encrypted_secret)
        await self._deliver(webhook_id, url, decrypted, "webhook.test", {"message": "Test ping from Odysseus"})

    async def _deliver(self, webhook_id: str, url: str, secret: Optional[str], event: str, payload: dict):
        """Internal delivery. Never call directly from outside this class (use deliver_test)."""
        # Re-validate URL at delivery time in case DB was tampered with
        try:
            validate_webhook_url(url)
        except ValueError as e:
            logger.warning(f"Webhook {webhook_id} has invalid URL, skipping: {e}")
            return

        body = json.dumps({"event": event, "timestamp": _utcnow().isoformat(), "data": payload})
        headers = {
            "Content-Type": "application/json",
            "X-Odysseus-Event": event,
            "User-Agent": "Odysseus-Webhook/1.0",
        }
        if secret:
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Odysseus-Signature"] = sig

        db = SessionLocal()
        try:
            resp = await self._client.post(url, content=body, headers=headers)
            db.query(Webhook).filter(Webhook.id == webhook_id).update({
                "last_triggered_at": _utcnow(),
                "last_status_code": resp.status_code,
                "last_error": None,
            })
            db.commit()
        except Exception as e:
            logger.warning(f"Webhook delivery failed for {webhook_id}")
            try:
                db.query(Webhook).filter(Webhook.id == webhook_id).update({
                    "last_triggered_at": _utcnow(),
                    "last_status_code": None,
                    "last_error": sanitize_error(str(e)),
                })
                db.commit()
            except Exception:
                db.rollback()
        finally:
            db.close()

    async def close(self):
        await self._client.aclose()
