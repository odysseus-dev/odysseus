"""Proxy de Navidrome en /music con SSO desde la cookie del hub.

Navidrome escucha en 127.0.0.1:8443 con BaseUrl="/music" y confía en X-Authenticated-User
solo desde 127.0.0.1 (ExtAuth.TrustedSources). Este proxy:
- descarta cualquier X-Authenticated-User que llegue del cliente;
- la añade solo si la cookie odysseus_music es válida y su invitación sigue viva;
- cambia "Navidrome" por "Música" en la página, el manifest y los textos del bundle (TITLE_SWAPS).
"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from src import invites, music_sso

SESSION_COOKIE = "odysseus_session"

UPSTREAM = "http://127.0.0.1:8443"
USER_HEADER = "X-Authenticated-User"
# Textos fijos del bundle de Navidrome (v0.64.2) que muestran el nombre. Si una actualización
# cambia el minificado, estos reemplazos dejan de aplicarse y el título vuelve a "Navidrome".
TITLE_SWAPS = [
    (b"<title>Navidrome</title>", "<title>Música</title>".encode("utf-8")),
    (b'document.title="Navidrome"', 'document.title="Música"'.encode("utf-8")),
    (b'children:["Navidrome ",', 'children:["Música ",'.encode("utf-8")),
    (b'children:a||"Navidrome"', 'children:a||"Música"'.encode("utf-8")),
]
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "trailers", "transfer-encoding", "upgrade"}


def _forward_headers(request: Request, user: str | None) -> dict:
    out = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP or lk in ("host", "content-length", USER_HEADER.lower()):
            continue
        if lk == "cookie":
            kept = [c.strip() for c in v.split(";") if c.strip()]
            kept = [c for c in kept if c.split("=", 1)[0] != music_sso.COOKIE_NAME]
            if not kept:
                continue
            # Sin strip, la cookie que queda tras quitar odysseus_music arrastra un espacio
            # inicial y httpx rechaza la cabecera (502 "Música no responde").
            v = "; ".join(kept)
        out[k] = v
    if user:
        out[USER_HEADER] = user
    return out


def _fix_body(path: str, ctype: str, body: bytes) -> bytes:
    if path.endswith("/manifest.webmanifest"):
        try:
            data = json.loads(body)
        except ValueError:
            return body
        if isinstance(data, dict) and data.get("name") == "Navidrome":
            data["name"] = "Música"
            data["short_name"] = "Música"
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return body
    if "text/html" in ctype or "javascript" in ctype:
        for old, new in TITLE_SWAPS:
            body = body.replace(old, new)
    return body


def _session_invite(request: Request) -> str | None:
    """Invitación de la sesión de Odysseus que hace la petición (None si no hay sesión viva)."""
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    token = request.cookies.get(SESSION_COOKIE)
    if auth_mgr is None or not token or not auth_mgr.validate_token(token):
        return None
    return auth_mgr.invite_id_for_token(token)


def setup_music_sso_routes() -> APIRouter:
    router = APIRouter()

    async def _proxy(request: Request):
        claims = music_sso.read(request.cookies.get(music_sso.COOKIE_NAME))
        # La cookie de Música sólo vale mientras la sesión de Odysseus sea de ESA misma
        # invitación: tras cerrar sesión (o entrar otra persona) no reenvía al usuario anterior.
        same_session = bool(claims) and _session_invite(request) == claims["i"]
        user = claims["u"] if same_session and invites.is_live(claims["i"]) else None
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        target = UPSTREAM + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        req = client.build_request(request.method, target,
                                   headers=_forward_headers(request, user),
                                   content=await request.body())
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse(status_code=502, content={"error": "Música no responde"})

        ctype = upstream.headers.get("content-type", "")
        rewrite = request.method != "HEAD" and (
            "text/html" in ctype or "javascript" in ctype or request.url.path.endswith("/manifest.webmanifest"))
        if rewrite:
            body = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            fixed = _fix_body(request.url.path, ctype, body)
            resp = Response(content=fixed, status_code=upstream.status_code)
            for k, v in upstream.headers.multi_items():
                if k.lower() not in _HOP and k.lower() not in ("content-length", "content-encoding"):
                    resp.headers.append(k, v)
            return resp

        resp = StreamingResponse(
            upstream.aiter_raw(), status_code=upstream.status_code,
            background=BackgroundTask(_close, upstream, client))
        for k, v in upstream.headers.multi_items():
            if k.lower() not in _HOP:
                resp.headers.append(k, v)
        return resp

    async def _close(upstream: httpx.Response, client: httpx.AsyncClient):
        await upstream.aclose()
        await client.aclose()

    @router.post("/api/music-sso/logout", include_in_schema=False)
    async def music_sso_logout(request: Request):
        """Cierra la sesión de Odysseus y borra la cookie SSO de /music (Cerrar sesión del hub)."""
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        token = request.cookies.get(SESSION_COOKIE)
        if auth_mgr is not None and token:
            auth_mgr.revoke_token(token)
        resp = JSONResponse(content={"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        resp.delete_cookie(music_sso.COOKIE_NAME, path=music_sso.COOKIE_PATH)
        return resp

    @router.api_route("/music", methods=METHODS, include_in_schema=False)
    async def music_root(request: Request):
        return await _proxy(request)

    @router.api_route("/music/{path:path}", methods=METHODS, include_in_schema=False)
    async def music_path(request: Request, path: str):
        return await _proxy(request)

    return router
