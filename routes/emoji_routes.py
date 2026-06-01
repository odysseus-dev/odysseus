# routes/emoji_routes.py
# Same-origin emoji SVG proxy. The frontend rewrites emoji in chat to a
#   <span class="emoji" style="--em:url('/api/emoji/<codepoints>.svg')">
# which uses the returned SVG as a CSS mask tinted to the text color, so emoji
# render as monochrome line icons (project rule: never colorful emoji). The
# black line-art SVGs are lazily fetched from the OpenMoji CDN on first use and
# cached on disk, so:
#   - the client only ever talks to our own origin (no CDN dep, no CSP change),
#   - the repo isn't bloated with thousands of SVG files,
#   - it works offline once an emoji has been seen once.
# Unknown/unreachable codepoints return a transparent SVG (not 404), so the CSS
# mask shows nothing rather than a solid currentColor box.
import logging
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.environ.get("ODYSSEUS_DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "emoji_cache"
# OpenMoji "black" set = monochrome line-art SVGs. Filenames are the codepoints
# in UPPERCASE (FE0F dropped, same as we compute), '-' joined.
_OPENMOJI_BASE = "https://cdn.jsdelivr.net/npm/openmoji@15.0.0/black/svg"
# codepoints like "1f600" or "1f468-200d-1f469-200d-1f467" (lowercase hex, '-' joined)
_CODE_RE = re.compile(r"^[0-9a-f]{2,6}(?:-[0-9a-f]{2,6})*$")
_SVG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
# Returned when a codepoint is unknown/unreachable: an empty (transparent) SVG,
# so the CSS mask renders nothing instead of a solid box. Not cached, so a later
# request can still pick up the real glyph once the CDN is reachable.
_BLANK_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>'
_BLANK_HEADERS = {"Cache-Control": "no-store"}


def setup_emoji_routes() -> APIRouter:
    router = APIRouter(prefix="/api/emoji", tags=["emoji"])

    def _blank() -> Response:
        return Response(_BLANK_SVG, media_type="image/svg+xml", headers=_BLANK_HEADERS)

    @router.get("/{code}.svg")
    async def emoji_svg(code: str):
        code = code.lower()
        if not _CODE_RE.match(code):
            return _blank()

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fp = _CACHE_DIR / f"{code}.svg"
        if fp.exists():
            return FileResponse(fp, media_type="image/svg+xml", headers=_SVG_HEADERS)

        # First time we've seen this emoji — fetch the OpenMoji black SVG + cache
        # it. OpenMoji filenames are the codepoints uppercased.
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{_OPENMOJI_BASE}/{code.upper()}.svg")
            if r.status_code == 200 and b"<svg" in r.content[:256]:
                try:
                    fp.write_bytes(r.content)
                except Exception:
                    pass  # cache write is best-effort
                return Response(r.content, media_type="image/svg+xml", headers=_SVG_HEADERS)
        except Exception as e:
            logger.warning("emoji fetch %s failed: %s", code, e)

        return _blank()

    return router
