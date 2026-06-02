"""Odysseus mobile companion bridge.

A thin, additive LAN layer that lets the `odysseus-mobile` Expo client pair to a
running Odysseus server and drive it over the local network. It deliberately adds
NO new LLM logic: the phone authenticates with a standard `ody_` API token (see
routes/api_token_routes.py) and talks to the existing chat/session endpoints.

This package only adds:
  - GET /api/companion/ping  — cheap, token-validated pairing health check
  - GET /api/companion/info  — server identity + capability flags for the client

Pairing tokens are minted out-of-band by an admin (Settings → API tokens, or
scripts/pair_mobile.py).
"""

from companion.routes import setup_companion_routes

__all__ = ["setup_companion_routes"]
