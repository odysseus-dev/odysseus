"""Credential resolution seam.

`llm_core` resolves a `Credential` from an `EndpointRef` right before each call.
Static-key endpoints are the identity case (the headers frozen on the ref ARE
the credential); OAuth endpoints resolve through the token store, which loads
and refreshes tokens keyed on `EndpointRef.endpoint_id`. The seam is async
(`resolve`) with a sync sibling (`resolve_sync`) for the 3 sync `llm_call`
callers.
"""
from src.providers.auth.credentials import Credential, resolve, resolve_sync

__all__ = ["Credential", "resolve", "resolve_sync"]
