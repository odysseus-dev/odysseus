"""Static-key credential resolution.

The headers frozen on the EndpointRef ARE the credential — this is the identity
function that makes wrapping today's tuple callers in an `EndpointRef` a no-op.
The value of routing through here is that the OAuth impl plugs in at the same
seam without touching the call path.
"""
from src.providers.auth.credentials import Credential
from src.providers.endpoint_ref import EndpointRef


def resolve(ref: EndpointRef) -> Credential:
    return Credential(headers=dict(ref.headers) if ref.headers else {})
