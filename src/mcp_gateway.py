"""MCP API gateway pool — round-robin selection with cooldown failover.

Odysseus already speaks MCP over stdio/SSE/Streamable-HTTP (``src/mcp_manager.py``).
What it does *not* have is a way to treat several interchangeable gateway
*endpoints* as one logical MCP server. This module adds that layer.

Why it exists
-------------
An MCP gateway (e.g. ``https://htp.sh/ai``) fronts a pool of upstream MCP
stacks. When one upstream is degraded, the gateway keeps answering but starts
failing tool calls. A single hardcoded gateway URL turns that partial outage
into a total one for us.

So we model a gateway as a *pool* of base URLs:

    pool = GatewayPool(["https://htp.sh/ai", "https://ai.htp.sh/ai"])
    ep   = pool.acquire()      # round-robin across non-cooling endpoints
    try:
        result = await pool.call(ep, "/ai/tools", json={...})
    except GatewayExhausted:
        ...                    # every endpoint cooling or dead

Failure handling
----------------
Adapted from the proven state machine in the ``ocmesh`` CLI (model rotation):
a failure puts the endpoint in an exponential-backoff *cooldown* keyed on
strike count; repeated terminal failures mark it *dead* until an operator
clears it. Success clears the cooldown and updates a running latency average.

Unlike ``ocmesh`` this runs inside the web server, so the state file is
guarded by both an in-process lock and an ``fcntl`` file lock — Odysseus can be
run with multiple workers against the same data directory, and a plain
read-modify-write would silently drop the other process's counters.

Security note
-------------
Gateway URLs are operator-supplied and are fetched server-side, which is the
same shape as the known SSRF gap recorded in ``THREAT_MODEL.md`` (#1039). To
avoid *adding* to it, ``_validate_base_url`` requires HTTPS and, unless
``ODYSSEUS_GATEWAY_ALLOW_PRIVATE=1``, rejects hosts that resolve to loopback,
link-local, or RFC1918 space. Private gateways must be opted into explicitly.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR

logger = logging.getLogger(__name__)

# --- tunables (env-overridable) -------------------------------------------
BASE_COOLDOWN = int(os.getenv("ODYSSEUS_GATEWAY_COOLDOWN", "60"))
MAX_COOLDOWN = int(os.getenv("ODYSSEUS_GATEWAY_COOLDOWN_MAX", "3600"))
ALLOW_PRIVATE = os.getenv("ODYSSEUS_GATEWAY_ALLOW_PRIVATE", "") in {"1", "true", "yes"}
REQUEST_TIMEOUT = float(os.getenv("ODYSSEUS_GATEWAY_TIMEOUT", "30"))

STATE_PATH = os.path.join(DATA_DIR, "gateway_state.json")

# Endpoint keys we understand from a gateway manifest (the JSON a gateway
# serves at its root, e.g. htp.sh/ai). Unknown keys are preserved in
# ``raw`` but never dispatched to.
MANIFEST_KEYS = (
    "models", "chat", "tools", "compose", "route",
    "capabilities", "abilities", "health", "metrics", "vitals",
    "mcp_resources", "mcp_read",
)


class GatewayError(Exception):
    """Base class for gateway pool failures."""


class GatewayExhausted(GatewayError):
    """Every endpoint is cooling down or dead."""


class GatewayRejected(GatewayError):
    """A gateway URL failed validation (SSRF guard)."""


@dataclass
class GatewayEndpoint:
    """One base URL in the pool, plus its live routing state."""
    base_url: str
    id: str = ""
    enabled: bool = True
    # Routing state — persisted, not config.
    strikes: int = 0
    cooldown_until: float = 0.0
    dead: bool = False
    ok_count: int = 0
    fail_count: int = 0
    avg_latency: float = 0.0
    last_error: str = ""
    manifest: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.base_url.rstrip("/")

    def cooling(self, now: Optional[float] = None) -> bool:
        return self.cooldown_until > (now if now is not None else time.time())

    def available(self, now: Optional[float] = None) -> bool:
        return self.enabled and not self.dead and not self.cooling(now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "strikes": self.strikes,
            "cooldown_until": self.cooldown_until,
            "dead": self.dead,
            "ok_count": self.ok_count,
            "fail_count": self.fail_count,
            "avg_latency": round(self.avg_latency, 4),
            "last_error": self.last_error,
        }


def _validate_base_url(raw: str) -> str:
    """Reject URLs we refuse to fetch server-side.

    HTTPS is mandatory (these carry bearer tokens). Private address space is
    blocked unless explicitly allowed, so an operator cannot use an admin
    endpoint as a pivot into the internal network.
    """
    url = (raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise GatewayRejected(f"gateway must be https, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise GatewayRejected("gateway URL has no host")

    if ALLOW_PRIVATE:
        return url.rstrip("/")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise GatewayRejected(f"cannot resolve gateway host {host!r}: {exc}") from exc

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise GatewayRejected(
                f"gateway host {host!r} resolves to non-public address {addr}; "
                "set ODYSSEUS_GATEWAY_ALLOW_PRIVATE=1 to permit"
            )
    return url.rstrip("/")


class GatewayPool:
    """Round-robin pool of MCP gateway endpoints with cooldown failover."""

    def __init__(self, base_urls: Optional[List[str]] = None, state_path: Optional[str] = None):
        self.state_path = state_path or STATE_PATH
        self._endpoints: Dict[str, GatewayEndpoint] = {}
        self._cursor = 0
        self._tlock = threading.Lock()
        for url in base_urls or []:
            self.add(url, persist=False)

    # -- membership --------------------------------------------------------

    def add(self, base_url: str, persist: bool = True) -> GatewayEndpoint:
        """Add an endpoint. Raises :class:`GatewayRejected` if URL is unsafe."""
        url = _validate_base_url(base_url)
        ep = self._endpoints.get(url)
        if ep is None:
            ep = GatewayEndpoint(base_url=url)
            self._endpoints[url] = ep
        if persist:
            self.save()
        return ep

    def remove(self, base_url: str) -> bool:
        url = (base_url or "").strip().rstrip("/")
        existed = self._endpoints.pop(url, None) is not None
        if existed:
            self.save()
        return existed

    def revive(self, base_url: str) -> bool:
        """Clear cooldown + dead flag for an endpoint (operator override)."""
        ep = self._endpoints.get((base_url or "").strip().rstrip("/"))
        if ep is None:
            return False
        with self._mutate():
            ep.dead = False
            ep.strikes = 0
            ep.cooldown_until = 0.0
            ep.last_error = ""
        return True

    @property
    def endpoints(self) -> List[GatewayEndpoint]:
        return list(self._endpoints.values())

    # -- state persistence -------------------------------------------------

    def load(self) -> None:
        """Restore routing counters from disk, preserving current membership."""
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        saved = {e.get("base_url"): e for e in data.get("endpoints", [])}
        for url, ep in self._endpoints.items():
            rec = saved.get(url)
            if not rec:
                continue
            ep.strikes = int(rec.get("strikes", 0))
            ep.cooldown_until = float(rec.get("cooldown_until", 0.0))
            ep.dead = bool(rec.get("dead", False))
            ep.ok_count = int(rec.get("ok_count", 0))
            ep.fail_count = int(rec.get("fail_count", 0))
            ep.avg_latency = float(rec.get("avg_latency", 0.0))

    @contextlib.contextmanager
    def _mutate(self):
        """Locked read-modify-write. Mutate state *inside* this block.

        The merge (``load()``) happens on entry, before the caller's writes,
        so a concurrent worker's counters are folded in without clobbering the
        change being made now. Reloading on exit instead would restore stale
        on-disk values over the caller's pending mutation.

        Mirrors the ``ocmesh`` CLI: a bare read-then-write drops the other
        process's counters when two workers share a data directory.
        """
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            fcntl = None  # type: ignore[assignment]

        # A threading.Lock (not asyncio.Lock): _mutate is called from sync code
        # paths too, and a coroutine lock would not serialize those.
        with self._tlock:
            lock_path = self.state_path + ".lock"
            try:
                os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
                with open(lock_path, "a+") as lock_fh:
                    if fcntl is not None:
                        fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        self.load()
                        yield
                    finally:
                        if fcntl is not None:
                            fcntl.flock(lock_fh, fcntl.LOCK_UN)
                        atomic_write_json(self.state_path, self._dump())
            except OSError:
                # Persistence is best-effort; routing still works in-memory.
                yield

    def _dump(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "endpoints": [ep.to_dict() for ep in self._endpoints.values()],
        }

    def save(self) -> None:
        """Flush current in-memory state to disk without re-merging.

        Callers that mutate counters must use ``with self._mutate():`` instead;
        this is only for flushes that change nothing.
        """
        with self._tlock:
            try:
                atomic_write_json(self.state_path, self._dump())
            except OSError:
                logger.debug("gateway state flush skipped: unwritable %s", self.state_path)

    # -- health accounting -------------------------------------------------

    def _record_ok(self, ep: GatewayEndpoint, latency: float) -> None:
        ep.ok_count += 1
        ep.avg_latency = (
            latency if not ep.ok_count or ep.avg_latency == 0.0
            else (ep.avg_latency * (ep.ok_count - 1) + latency) / ep.ok_count
        )
        ep.strikes = 0
        ep.cooldown_until = 0.0
        ep.dead = False
        ep.last_error = ""

    def _record_fail(self, ep: GatewayEndpoint, error: str, terminal: bool = False) -> None:
        ep.fail_count += 1
        ep.last_error = error[:400]
        if terminal:
            ep.dead = True
            return
        ep.strikes += 1
        backoff = min(BASE_COOLDOWN * (2 ** (ep.strikes - 1)), MAX_COOLDOWN)
        ep.cooldown_until = time.time() + backoff

    # -- selection ---------------------------------------------------------

    def acquire(self) -> GatewayEndpoint:
        """Next available endpoint in round-robin order.

        Raises :class:`GatewayExhausted` when nothing is routable.
        """
        now = time.time()
        candidates = [ep for ep in self._endpoints.values() if ep.available(now)]
        if not candidates:
            raise GatewayExhausted(
                f"no gateway endpoint available "
                f"({len(self._endpoints)} configured, all cooling or dead)"
            )
        # Round-robin over the currently-routable set, advancing the cursor so
        # repeated acquires spread load rather than pinning the first entry.
        candidates.sort(key=lambda e: e.id)
        idx = self._cursor % len(candidates)
        self._cursor = (idx + 1) % len(candidates)
        return candidates[idx]

    def peek(self) -> Optional[GatewayEndpoint]:
        try:
            return self.acquire()
        except GatewayExhausted:
            return None

    def status(self) -> Dict[str, Any]:
        now = time.time()
        eps = self._endpoints.values()
        return {
            "count": len(eps),
            "available": sum(1 for e in eps if e.available(now)),
            "cooling": sum(1 for e in eps if e.cooling(now)),
            "dead": sum(1 for e in eps if e.dead),
            "endpoints": [ep.to_dict() for ep in eps],
        }

    # -- HTTP --------------------------------------------------------------

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        tries: int = 2,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        """Send a request, failing over across endpoints on error.

        Each attempt picks a fresh endpoint via :meth:`acquire`, so a cooling
        endpoint is skipped rather than retried.
        """
        import httpx

        last_exc: Optional[Exception] = None
        for _ in range(max(1, tries)):
            try:
                ep = self.acquire()
            except GatewayExhausted:
                if last_exc:
                    raise GatewayExhausted(str(last_exc)) from last_exc
                raise

            url = ep.base_url + ("" if path.startswith("/") else "/") + path
            started = time.time()
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    resp = await client.request(
                        method, url, json=json_body, headers=headers
                    )
                latency = time.time() - started
                # 5xx and 429 are the gateway's fault, not the caller's; treat
                # them as cooling-triggering. 4xx is the caller's fault and
                # must NOT take the endpoint out of rotation.
                if resp.status_code >= 500 or resp.status_code == 429:
                    with self._mutate():
                        self._record_fail(
                            ep, f"HTTP {resp.status_code}",
                            terminal=resp.status_code >= 502,
                        )
                    last_exc = GatewayError(f"{url} returned HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 400:
                    # Client error: surface it, but the endpoint is fine.
                    raise GatewayError(f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}")

                with self._mutate():
                    self._record_ok(ep, latency)
                try:
                    return resp.json()
                except ValueError:
                    return resp.text

            except GatewayError:
                raise
            except Exception as exc:
                with self._mutate():
                    self._record_fail(ep, f"{type(exc).__name__}: {exc}")
                last_exc = exc
                logger.warning("gateway %s failed: %s", ep.id, exc)

        raise GatewayExhausted(
            f"all gateway attempts failed; last error: {last_exc}"
        ) from last_exc

    async def discover(self, base_url: str) -> Dict[str, Any]:
        """Fetch and cache a gateway manifest (the endpoint map it advertises).

        A gateway publishes its own routes as JSON at its root. Reading them
        rather than hardcoding paths means a gateway that adds or renames an
        endpoint does not require a code change here.
        """
        url = _validate_base_url(base_url)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.get(url + "/")
        resp.raise_for_status()
        payload = resp.json()

        ep = self._endpoints.get(url)
        if ep is not None:
            manifest = {
                k: payload["endpoints"][k]
                for k in MANIFEST_KEYS
                if isinstance(payload.get("endpoints"), dict) and k in payload["endpoints"]
            }
            ep.manifest = manifest
            self.save()
        return payload

    def resolve_path(self, base_url: str, key: str) -> Optional[str]:
        """Map a manifest key (``tools``, ``route``, ...) to a concrete path."""
        ep = self._endpoints.get((base_url or "").strip().rstrip("/"))
        if ep is None:
            return None
        return ep.manifest.get(key)


# --------------------------------------------------------------------------
# Process-wide default pool, built from the environment.
# --------------------------------------------------------------------------

_DEFAULT: Optional[GatewayPool] = None


def default_base_urls() -> List[str]:
    """Gateways configured via ``ODYSSEUS_GATEWAY_URLS`` (comma separated)."""
    raw = os.getenv("ODYSSEUS_GATEWAY_URLS", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip()]


def get_pool() -> GatewayPool:
    """Return the process-wide pool, created and restored on first use."""
    global _DEFAULT
    if _DEFAULT is None:
        pool = GatewayPool(default_base_urls())
        pool.load()
        _DEFAULT = pool
    return _DEFAULT


def set_pool(pool: GatewayPool) -> None:
    """Replace the process-wide pool (used by tests and admin reconfigure)."""
    global _DEFAULT
    _DEFAULT = pool