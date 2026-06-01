"""Credential interface + dispatch + the OAuth guard.

`Credential.headers` are merged into the outgoing request by the transport's
`build_headers`. For static-key providers the resolved credential is simply the
headers carried on the `EndpointRef`; OAuth providers resolve through the token
store (lookup + refresh-on-expiry keyed on `endpoint_id`).
"""
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import HTTPException

from src.providers.endpoint_ref import EndpointRef
from src.providers.spec import ProviderSpec


@dataclass(frozen=True)
class Credential:
    headers: Dict[str, str] = field(default_factory=dict)


def _effective_auth_type(ref: EndpointRef, spec: Optional[ProviderSpec]) -> str:
    """The spec is authoritative; the ref's auth_type is a fallback hint."""
    if spec is not None:
        return spec.auth_type
    return ref.auth_type


def _guard_oauth(ref: EndpointRef, spec: Optional[ProviderSpec]) -> None:
    """OAuth providers MUST have endpoint identity (so the token can be looked up
    and refreshed). A ref built from a legacy tuple has none — fail loudly rather
    than silently re-freezing a bearer token that can never refresh."""
    if _effective_auth_type(ref, spec) == "oauth" and not ref.endpoint_id:
        pid = getattr(spec, "id", None) or ref.provider_id or "?"
        raise HTTPException(
            500,
            f"Provider '{pid}' uses OAuth but this endpoint ref has no identity "
            f"(it came from a legacy tuple). Resolve it via resolve_endpoint_ref().",
        )


async def resolve(ref: EndpointRef, spec: Optional[ProviderSpec] = None) -> Credential:
    """Async credential resolution — static-key identity or OAuth lookup/refresh."""
    _guard_oauth(ref, spec)
    if _effective_auth_type(ref, spec) == "oauth":
        # Look up the token by ref.endpoint_id, refresh on expiry (single-flight
        # per endpoint), and build the bearer + account-id headers.
        from src.providers.auth import oauth_store  # lazy: avoids import cycle
        return await oauth_store.resolve_oauth_credential(ref, spec)
    from src.providers.auth import static  # lazy: avoids auth-package import cycle
    return static.resolve(ref)


def resolve_sync(ref: EndpointRef, spec: Optional[ProviderSpec] = None) -> Credential:
    """Sync sibling for the 3 sync `llm_call` callers. Static-key only.

    OAuth endpoints are deliberately unreachable from sync paths — refresh is
    async, and the sync callers (background utility work) never target a
    user-subscription endpoint. This is a documented boundary, not an oversight.
    """
    _guard_oauth(ref, spec)
    if _effective_auth_type(ref, spec) == "oauth":
        raise HTTPException(
            501,
            "OAuth endpoints require async credential resolution and are "
            "not reachable from sync llm_call paths",
        )
    from src.providers.auth import static  # lazy: avoids auth-package import cycle
    return static.resolve(ref)
