"""Odysseus companion bridge — additive, read-only LAN endpoints.

Exposes /api/companion/ping, /info, and an owner-scoped /models so a LAN client
can discover what a server offers. No new LLM logic; auth is enforced by the
existing AuthMiddleware. See companion/README.md.
"""

from companion.routes import setup_companion_routes

__all__ = ["setup_companion_routes"]
