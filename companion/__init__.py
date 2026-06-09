"""Odysseus companion bridge — additive LAN endpoints.

Read endpoints (/api/companion/ping, /info, owner-scoped /models) so a LAN
client can discover what a server offers, plus admin-only pairing
(/api/companion/pair) that mints a one-time chat-scoped token on POST. No new LLM
logic; auth is enforced by the existing AuthMiddleware. See companion/README.md.
"""

from companion.routes import setup_companion_routes

__all__ = ["setup_companion_routes"]
