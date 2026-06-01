"""Localization API — exposes the available locales and Accept-Language
negotiation so the client (and any future server-rendered first paint) can pick
a sensible default. The catalog JSON itself is served as static files from
/static/locales/<code>.json; this router only adds the registry + negotiation
that static hosting can't.
"""
from fastapi import APIRouter, Request

from core.i18n import available_locales, default_locale, negotiate


def setup_i18n_routes():
    router = APIRouter(prefix="/api/i18n", tags=["i18n"])

    @router.get("/locales")
    async def list_locales(request: Request):
        """Available locales plus the best match for this request's
        Accept-Language header (the suggested default before any user choice)."""
        accept = request.headers.get("accept-language")
        return {
            "default": default_locale(),
            "negotiated": negotiate(accept),
            "locales": available_locales(),
        }

    return router
