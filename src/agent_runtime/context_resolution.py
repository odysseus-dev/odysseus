"""Effective model context window, resolved once per logical turn.

A turn resolves the window it budgets against at preparation time, before any
model request, and keeps the value together with the evidence that chose it.
Terminal metrics report that stored resolution; they never start discovery.

Evidence classes are kept apart instead of being folded into one "known" flag:

* ``runtime_confirmed``: the serving process reported its active window
  (llama.cpp ``/slots`` or ``/props``) or rejected a request of this turn with
  an explicit limit.
* ``provider_advertised``: the provider's model catalog lists a window.
* ``operator_declared``: the client or operator declared a transport window.
  It caps runtime or provider evidence and replaces weaker evidence.
* ``known_table``: the static ``KNOWN_CONTEXT_WINDOWS`` fallback.
* ``unknown``: nothing above is available. The value is 0, never a default.

Any disagreement between sources is recorded as a conflict. An operator value
below a measured value is a cap, not a contradiction; an operator value above
it is a contradiction.

Context sizing is not authority: nothing here grants or denies an operation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
import logging
import time
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Upper bound for all provider metadata I/O of one turn preparation. A slow
# or unreachable metadata endpoint costs at most this much before the turn
# proceeds with whatever evidence it has.
PROBE_DEADLINE_SECONDS = 3.0
# Remote provider metadata changes rarely; failures are retried sooner so a
# transient outage does not pin a turn to weaker evidence for long. Local
# servers are always re-probed because they can restart with another window.
PROBE_CACHE_TTL_SECONDS = 600.0
PROBE_FAILURE_TTL_SECONDS = 60.0

# Headers that describe the chat request body rather than the caller.
_REQUEST_ONLY_HEADERS = frozenset({"content-type", "content-length", "accept", "accept-encoding"})


class ContextEvidence(str, Enum):
    RUNTIME_CONFIRMED = "runtime_confirmed"
    PROVIDER_ADVERTISED = "provider_advertised"
    OPERATOR_DECLARED = "operator_declared"
    KNOWN_TABLE = "known_table"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ContextObservation:
    evidence: ContextEvidence
    value: int
    source: str

    def to_dict(self) -> dict:
        return {"evidence": self.evidence.value, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class ContextConflict:
    first: ContextObservation
    second: ContextObservation

    def to_dict(self) -> dict:
        return {"first": self.first.to_dict(), "second": self.second.to_dict()}


@dataclass(frozen=True)
class ContextResolution:
    """The effective window of one turn and why it was chosen."""

    effective: int
    evidence: ContextEvidence
    source: str
    observations: tuple[ContextObservation, ...] = ()
    conflicts: tuple[ContextConflict, ...] = ()
    provider_io: bool = False
    cached: bool = False
    probe_errors: tuple[str, ...] = ()
    # The route this resolution describes. Empty for resolutions built
    # directly from observations by internal callers. The URL can carry
    # credentials, so it stays out of repr() and to_dict().
    endpoint_url: str = field(default="", repr=False)
    model: str = ""

    @property
    def mismatch(self) -> bool:
        return bool(self.conflicts)

    @property
    def budget_limit(self) -> int:
        """Window the runtime may budget against; 0 means budget reactively."""
        return self.effective if self.evidence is not ContextEvidence.UNKNOWN else 0

    @property
    def shaping_window(self) -> int:
        """Window for the legacy history compaction/trim helpers.

        Those helpers predate typed evidence and always size against some
        window, using DEFAULT_CONTEXT when none is known. This only feeds them
        a number; it never creates provenance for that number.
        """
        if self.budget_limit:
            return self.budget_limit
        from src.model_context import DEFAULT_CONTEXT
        return DEFAULT_CONTEXT

    def applies_to(self, endpoint_url: str, model: str) -> bool:
        """Whether this resolution may be reused for the given route."""
        if not self.endpoint_url and not self.model:
            return True
        return self.endpoint_url == endpoint_url and self.model == model

    def observe_runtime_limit(self, limit: Any, source: str = "provider_rejection") -> "ContextResolution":
        """Fold a limit the provider stated during this turn. Performs no I/O."""
        try:
            value = int(limit or 0)
        except (TypeError, ValueError):
            return self
        if value <= 0:
            return self
        observation = ContextObservation(ContextEvidence.RUNTIME_CONFIRMED, value, source)
        if observation in self.observations:
            return self
        combined = combine_observations((*self.observations, observation))
        return replace(
            combined,
            provider_io=self.provider_io,
            cached=self.cached,
            probe_errors=self.probe_errors,
            endpoint_url=self.endpoint_url,
            model=self.model,
        )

    def to_dict(self) -> dict:
        return {
            "effective": self.effective,
            "evidence": self.evidence.value,
            "source": self.source,
            "mismatch": self.mismatch,
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "observations": [observation.to_dict() for observation in self.observations],
            "provider_io": self.provider_io,
            "cached": self.cached,
            "probe_errors": list(self.probe_errors),
        }


UNRESOLVED_CONTEXT = ContextResolution(0, ContextEvidence.UNKNOWN, "none")

_MEASURED = (ContextEvidence.RUNTIME_CONFIRMED, ContextEvidence.PROVIDER_ADVERTISED)


def _conflicts(observations: tuple[ContextObservation, ...]) -> tuple[ContextConflict, ...]:
    conflicts = []
    for index, first in enumerate(observations):
        for second in observations[index + 1:]:
            if first.value == second.value:
                continue
            classes = {first.evidence, second.evidence}
            if ContextEvidence.OPERATOR_DECLARED in classes:
                operator, other = (
                    (first, second) if first.evidence is ContextEvidence.OPERATOR_DECLARED
                    else (second, first)
                )
                # A declared window replaces the static table and may cap a
                # measured window. Only a declaration above what the runtime
                # or provider supports contradicts it.
                if other.evidence not in _MEASURED or operator.value < other.value:
                    continue
            conflicts.append(ContextConflict(first, second))
    return tuple(conflicts)


def _strongest(observations, evidence: ContextEvidence) -> Optional[ContextObservation]:
    matching = [observation for observation in observations if observation.evidence is evidence]
    return min(matching, key=lambda observation: observation.value) if matching else None


def combine_observations(observations) -> ContextResolution:
    """Choose the effective window deterministically from observations.

    The smallest runtime-confirmed value wins, else the smallest provider
    value. An operator declaration caps either, and replaces the known table
    or an unknown window. The known table is used only when nothing stronger
    exists. No observation yields an unknown window of 0.
    """
    observations = tuple(observations)
    measured = (
        _strongest(observations, ContextEvidence.RUNTIME_CONFIRMED)
        or _strongest(observations, ContextEvidence.PROVIDER_ADVERTISED)
    )
    operator = _strongest(observations, ContextEvidence.OPERATOR_DECLARED)
    if measured and operator:
        chosen = operator if operator.value < measured.value else measured
    else:
        chosen = measured or operator or _strongest(observations, ContextEvidence.KNOWN_TABLE)
    conflicts = _conflicts(observations)
    if chosen is None:
        return replace(UNRESOLVED_CONTEXT, observations=observations, conflicts=conflicts)
    return ContextResolution(
        chosen.value, chosen.evidence, chosen.source,
        observations=observations, conflicts=conflicts,
    )


# ---------------------------------------------------------------------------
# Provider metadata probe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ProbeResult:
    observations: tuple[ContextObservation, ...] = ()
    errors: tuple[str, ...] = ()
    io: bool = False


_probe_cache: dict[tuple[str, str, str], tuple[float, _ProbeResult]] = {}


def clear_probe_cache() -> None:
    _probe_cache.clear()


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, Optional[int]]:
    parsed = urlparse(url or "")
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError:
        return ("", "", None)
    return (scheme, (parsed.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme))


def _http_client(timeout: float):
    # Credentials must never follow a redirect to another location.
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _provider_urls(endpoint_url: str) -> tuple[Optional[str], str]:
    """Models catalog URL and the server-resolved form of the endpoint.

    Both come from the existing endpoint resolver, which may rewrite an
    unresolvable host to its Tailscale address. Blocking (DNS, subprocess);
    call it off the event loop.
    """
    from src.endpoint_resolver import build_models_url, resolve_url

    return build_models_url(endpoint_url), resolve_url(endpoint_url)


def _probe_headers(trusted_origins, target_url: str, headers: Optional[Mapping[str, Any]]) -> dict:
    """Forward the turn's provider credentials only to the provider's origin."""
    origin = _origin(target_url)
    if not headers or not origin[1] or origin not in trusted_origins:
        return {}
    return {
        str(name): str(value) for name, value in headers.items()
        if value is not None and str(name).lower() not in _REQUEST_ONLY_HEADERS
    }


def _auth_fingerprint(headers: Optional[Mapping[str, Any]]) -> str:
    if not headers:
        return ""
    material = json.dumps(
        sorted((str(k).lower(), str(v)) for k, v in headers.items()
               if v is not None and str(k).lower() not in _REQUEST_ONLY_HEADERS),
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _serving_base(endpoint_url: str) -> str:
    # Same derivation the regular runtime uses for llama.cpp server routes.
    return endpoint_url.split("/v1")[0] if "/v1" in endpoint_url else endpoint_url.rsplit("/", 1)[0]


async def _get_json(client, url, headers, errors, label):
    try:
        response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        errors.append(f"{label}:timeout")
        return None
    except httpx.TransportError:
        errors.append(f"{label}:transport_error")
        return None
    status = getattr(response, "status_code", 0)
    if not (200 <= int(status or 0) < 300):
        errors.append(f"{label}:http_{status}")
        return None
    try:
        return response.json()
    except Exception:
        errors.append(f"{label}:invalid_payload")
        return None


def _positive_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return 0
    return int(value)


async def _probe(endpoint_url, model, headers, is_local, observations, errors, timeout):
    from src.copilot import is_copilot_base
    from src.model_context import _model_ctx_from_entry

    # Credentials go only to the configured provider's origin, or to the
    # form of that same endpoint the server-owned resolver produced.
    trusted = {_origin(endpoint_url)}
    async with _http_client(timeout) as client:
        if is_local:
            base = _serving_base(endpoint_url)
            slots = await _get_json(
                client, f"{base}/slots", _probe_headers(trusted, f"{base}/slots", headers),
                errors, "slots",
            )
            n_ctx = _positive_int(slots[0].get("n_ctx")) if (
                isinstance(slots, list) and slots and isinstance(slots[0], dict)
            ) else 0
            if not n_ctx:
                props = await _get_json(
                    client, f"{base}/props", _probe_headers(trusted, f"{base}/props", headers),
                    errors, "props",
                )
                generation = props.get("default_generation_settings") if isinstance(props, dict) else None
                n_ctx = _positive_int(generation.get("n_ctx")) if isinstance(generation, dict) else 0
                source = "llamacpp_props"
            else:
                source = "llamacpp_slots"
            if n_ctx:
                observations.append(
                    ContextObservation(ContextEvidence.RUNTIME_CONFIRMED, n_ctx, source)
                )

        # Copilot's catalog needs headers this layer does not own; an
        # unauthenticated probe only fails. Its models are table-covered.
        if is_copilot_base(endpoint_url):
            errors.append("models:unsupported_endpoint")
            return
        # URL building may resolve the host (DNS, tailscale lookup); keep that
        # off the event loop and inside the probe deadline.
        models_url, resolved_endpoint = await asyncio.to_thread(_provider_urls, endpoint_url)
        if not models_url:
            errors.append("models:unsupported_endpoint")
            return
        trusted.add(_origin(resolved_endpoint))
        payload = await _get_json(
            client, models_url, _probe_headers(trusted, models_url, headers),
            errors, "models",
        )
        if payload is None:
            return
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            errors.append("models:invalid_payload")
            return
        wanted = model.split("/")[-1]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id") or "")
            if entry_id == model or entry_id.split("/")[-1] == wanted:
                value = _model_ctx_from_entry(entry)
                if value:
                    observations.append(ContextObservation(
                        ContextEvidence.PROVIDER_ADVERTISED, int(value), "models_catalog",
                    ))
                else:
                    errors.append("models:no_window_listed")
                return
        errors.append("models:model_not_listed")


async def probe_provider_context(
    endpoint_url: str,
    model: str,
    *,
    headers: Optional[Mapping[str, Any]] = None,
    deadline_seconds: float = PROBE_DEADLINE_SECONDS,
    is_local: Optional[bool] = None,
) -> _ProbeResult:
    """Query provider metadata once, bounded by ``deadline_seconds``.

    Never raises: every failure is reported as a short, secret-free error code
    so a turn can continue with other evidence.
    """
    observations: list[ContextObservation] = []
    errors: list[str] = []
    if is_local is None:
        is_local = await _is_local(endpoint_url)
    timeout = max(0.1, float(deadline_seconds))
    try:
        await asyncio.wait_for(
            _probe(endpoint_url, model, headers, is_local, observations, errors, timeout),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        errors.append("deadline_exceeded")
    except Exception as exc:
        logger.debug("Context window probe failed: %s", type(exc).__name__)
        errors.append("probe_failed")
    return _ProbeResult(tuple(observations), tuple(errors), io=True)


async def _is_local(endpoint_url: str) -> bool:
    from src.model_context import is_local_endpoint

    try:
        # Reads configured endpoints from the local database on the calling
        # thread, as the regular runtime does. Moving it to worker threads
        # gives SQLite sessions per-thread connections the app never uses.
        return bool(is_local_endpoint(endpoint_url))
    except Exception:
        return False


async def _cached_probe(endpoint_url, model, headers, deadline_seconds, clock):
    is_local = await _is_local(endpoint_url)
    key = (endpoint_url, model, _auth_fingerprint(headers))
    if not is_local:
        cached = _probe_cache.get(key)
        if cached and cached[0] > clock():
            return cached[1], True
    result = await probe_provider_context(
        endpoint_url, model, headers=headers, deadline_seconds=deadline_seconds,
        is_local=is_local,
    )
    if not is_local:
        ttl = PROBE_CACHE_TTL_SECONDS if result.observations else PROBE_FAILURE_TTL_SECONDS
        _probe_cache[key] = (clock() + ttl, result)
    return result, False


def declared_context_window(client_runtime_context: Any) -> int:
    """Operator/client declared transport window, or 0."""
    if not isinstance(client_runtime_context, Mapping):
        return 0
    try:
        value = int(client_runtime_context.get("model_context_window") or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


async def resolve_effective_context(
    endpoint_url: str,
    model: str,
    *,
    headers: Optional[Mapping[str, Any]] = None,
    client_runtime_context: Any = None,
    deadline_seconds: float = PROBE_DEADLINE_SECONDS,
    probe: bool = True,
    clock=time.monotonic,
) -> ContextResolution:
    """Resolve the effective context window for one turn preparation."""
    from src.model_context import _lookup_known

    observations: list[ContextObservation] = []
    errors: tuple[str, ...] = ()
    provider_io = cached = False
    if probe and endpoint_url and model:
        result, cached = await _cached_probe(
            endpoint_url, model, headers, deadline_seconds, clock,
        )
        observations.extend(result.observations)
        errors = result.errors
        provider_io = result.io and not cached
    declared = declared_context_window(client_runtime_context)
    if declared:
        observations.append(ContextObservation(
            ContextEvidence.OPERATOR_DECLARED, declared, "client_runtime_context",
        ))
    known = _lookup_known(model or "")
    if known:
        observations.append(ContextObservation(ContextEvidence.KNOWN_TABLE, int(known), "known_table"))
    resolution = combine_observations(observations)
    resolution = replace(
        resolution, provider_io=provider_io, cached=cached, probe_errors=errors,
        endpoint_url=endpoint_url or "", model=model or "",
    )
    if resolution.mismatch:
        logger.info(
            "Context window sources disagree for %s: %s",
            model, [conflict.to_dict() for conflict in resolution.conflicts],
        )
    return resolution


def context_metrics(resolution: Optional[ContextResolution], request_tokens: int) -> dict:
    """Metrics fields derived only from a stored resolution. Performs no I/O."""
    resolution = resolution or UNRESOLVED_CONTEXT
    length = resolution.budget_limit
    percent = (
        min(round((request_tokens / length) * 100, 1), 100.0)
        if length and request_tokens else 0
    )
    return {
        "context_length": length,
        "context_percent": percent,
        "context_resolution": resolution.to_dict(),
    }
