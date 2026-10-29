"""Health audit: SSE client disconnects (e.g. /api/editor/bridge/events) surface
as anyio.EndOfStream through Starlette's BaseHTTPMiddleware. The slow-request
middleware must map that to a client-side 499, not a logged 500 traceback."""
import asyncio

import anyio
from starlette.requests import Request

from app import _SlowRequestLogMiddleware


def _request(path):
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def test_stream_disconnect_is_mapped_to_499():
    async def call_next(_request):
        raise anyio.EndOfStream

    mw = _SlowRequestLogMiddleware(lambda *a: None)
    resp = asyncio.run(mw.dispatch(_request("/api/editor/bridge/events"), call_next))
    assert resp.status_code == 499


def test_real_responses_pass_through():
    from starlette.responses import JSONResponse

    async def call_next(_request):
        return JSONResponse({"ok": True}, status_code=200)

    mw = _SlowRequestLogMiddleware(lambda *a: None)
    resp = asyncio.run(mw.dispatch(_request("/api/health"), call_next))
    assert resp.status_code == 200
