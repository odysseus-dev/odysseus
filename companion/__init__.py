"""Odysseus companion bridge — additive LAN endpoints.

Read endpoints (/api/companion/ping, /info, owner-scoped /models) so a LAN
client can discover what a server offers, admin-only pairing (/api/companion/pair)
that mints a one-time chat-scoped token on POST, and an owner-scoped Deep Research
launcher (/api/companion/research/*) so a paired phone can start, watch, cancel,
and read research runs. No new LLM logic; auth is enforced by the existing
AuthMiddleware. See companion/README.md.
"""

from companion.routes import setup_companion_routes

__all__ = ["setup_companion_routes"]
