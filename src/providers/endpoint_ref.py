"""EndpointRef — endpoint identity + credential source for one dispatch target.

The load-bearing reason this exists: today's call path freezes auth *headers* at
resolve time. An OAuth provider can't refresh a token from a frozen header — it
needs endpoint IDENTITY (which DB endpoint / which owner) so it can look up and
refresh the credential per call. `EndpointRef` carries that identity alongside
the static headers.

`from_legacy()` wraps the historical `(url, model, headers)` tuple. It has no
`endpoint_id`, so it is **static-compatible but NOT OAuth-complete** — a
documented boundary, not a gap. The auth resolver raises if an OAuth provider is
handed a legacy ref (see providers.auth.credentials).
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class EndpointRef:
    url: str
    model: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    endpoint_id: Optional[str] = None      # DB identity — REQUIRED for OAuth refresh
    owner: Optional[str] = None
    provider_id: Optional[str] = None       # ProviderSpec.id, when known
    auth_type: str = "api_key"              # "api_key" | "oauth"

    @classmethod
    def from_legacy(cls, url: str, model: Optional[str] = None,
                    headers: Optional[Dict[str, str]] = None) -> "EndpointRef":
        """Build a STATIC-creds ref from the legacy (url, model, headers) tuple.

        No endpoint identity → static-compatible, not OAuth-complete.
        """
        return cls(url=url, model=model, headers=headers, auth_type="api_key")
