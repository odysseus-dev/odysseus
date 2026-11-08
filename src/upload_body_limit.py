"""Request-body byte cap for the multipart upload routes, enforced before parsing.

FastAPI parses a multipart body -- spooling every file part to disk -- before
the route handler runs, and the per-file size check in
``UploadHandler.save_upload`` only runs after that. With no body cap anywhere,
a request far over the upload limit was written to disk in full only to be
refused afterwards. This pure-ASGI middleware refuses an upload request with
413 as soon as its declared ``Content-Length`` passes the cap, or, for a body
sent without ``Content-Length`` (chunked), as soon as the running count of the
received body chunks passes it. It never buffers the body itself, and requests
to any other route pass through untouched.
"""

from typing import Optional

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.middleware import get_application_route_path
from src.upload_limits import format_byte_limit, get_chat_upload_max_bytes

# The chat composer attaches at most MAX_FILES (10) files per request
# (static/js/fileHandler.js).
CHAT_UPLOAD_MAX_FILES = 10

# Headroom for multipart boundaries, per-part headers and the small form fields
# (session_id, gallery_id) that travel next to the file bytes.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

# POST routes that take multipart uploads saved through
# UploadHandler.save_upload, with the number of files each one accepts.
UPLOAD_BODY_ROUTES = {
    "/api/upload": CHAT_UPLOAD_MAX_FILES,
    "/api/documents/import-pdf": 1,
    "/api/documents/import-docx": 1,
}


def upload_body_cap(path: str) -> Optional[int]:
    """Return the request-body byte cap for an upload route, or None for other routes."""
    max_files = UPLOAD_BODY_ROUTES.get(path.rstrip("/") or "/")
    if max_files is None:
        return None
    return get_chat_upload_max_bytes() * max_files + MULTIPART_OVERHEAD_BYTES


class UploadBodyTooLarge(HTTPException):
    """Raised from ``receive`` once a streamed upload body passes its cap.

    An HTTPException so FastAPI's body parsing re-raises it unchanged instead
    of reporting a generic 400 parse error.
    """

    def __init__(self, cap: int) -> None:
        super().__init__(status_code=413, detail=_too_large_detail(cap))


def _too_large_detail(cap: int) -> str:
    return f"Upload request body exceeds the {format_byte_limit(cap)} limit"


def _too_large_response(cap: int) -> JSONResponse:
    return JSONResponse({"detail": _too_large_detail(cap)}, status_code=413)


def _declared_length(scope: Scope) -> Optional[int]:
    for name, value in scope.get("headers") or ():
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class UploadBodyLimitMiddleware:
    """Refuse upload requests whose body is larger than the upload cap with 413."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        cap = upload_body_cap(get_application_route_path(scope))
        if cap is None:
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > cap:
            # Refuse on the declared size alone: the app never runs and no
            # byte of the body is read.
            await _too_large_response(cap)(scope, receive, send)
            return

        received = 0
        exceeded = False
        response_started = False
        response_replaced = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise UploadBodyTooLarge(cap)
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    exceeded = True
                    raise UploadBodyTooLarge(cap)
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started, response_replaced
            if response_replaced:
                return
            if message["type"] == "http.response.start":
                if exceeded:
                    # Whatever the app made of the aborted body, the client
                    # gets the 413.
                    response_replaced = True
                    await _too_large_response(cap)(scope, receive, send)
                    return
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except UploadBodyTooLarge:
            if response_started or response_replaced:
                raise
            await _too_large_response(cap)(scope, receive, send)
