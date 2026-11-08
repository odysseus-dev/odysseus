"""Upload request bodies are capped before FastAPI parses them.

The per-file size check in UploadHandler.save_upload only runs after FastAPI
has parsed the multipart body, spooling every part to disk, and nothing capped
the body itself: a 50 MiB upload was written to disk in full before the 10 MB
check refused it. UploadBodyLimitMiddleware (src/upload_body_limit.py) refuses
an upload request with 413 on its declared Content-Length, or, for a chunked
body, as soon as the received chunks pass the cap.
"""
import asyncio
import types

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import routes.upload_routes as upload_routes
from src.upload_body_limit import (
    MULTIPART_OVERHEAD_BYTES,
    UploadBodyLimitMiddleware,
    upload_body_cap,
)

MAX_FILE_BYTES = 64 * 1024
CHAT_CAP = MAX_FILE_BYTES * 10 + MULTIPART_OVERHEAD_BYTES
BOUNDARY = "odysseusboundary"
CHUNK = 64 * 1024


@pytest.fixture(autouse=True)
def _small_upload_limit(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ODYSSEUS_CHAT_UPLOAD_MAX_BYTES", str(MAX_FILE_BYTES))


@pytest.fixture
def upload_app(monkeypatch):
    """The real chat upload route behind the middleware, with a save_upload spy."""
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload"))
    saved = []
    handler = types.SimpleNamespace(upload_rate_log={}, max_concurrent_uploads=3)

    def save_upload(u, client_ip, owner=None):
        saved.append(u.filename)
        return {
            "id": "0" * 32 + ".txt",
            "name": u.filename,
            "mime": "text/plain",
            "size": 5,
            "hash": "h",
            "uploaded_at": "now",
            "width": None,
            "height": None,
            "is_duplicate": False,
        }

    handler.save_upload = save_upload
    router, _cleanup = upload_routes.setup_upload_routes(handler)
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(UploadBodyLimitMiddleware)
    return app, saved


def _multipart(file_bytes: bytes, filename: str = "note.txt") -> bytes:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode()
    return head + file_bytes + f"\r\n--{BOUNDARY}--\r\n".encode()


def _call_asgi(app, path, chunks, content_length=None):
    """Drive one POST straight through the ASGI app, counting body reads."""
    headers = [(b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())]
    if content_length is None:
        headers.append((b"transfer-encoding", b"chunked"))
    else:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("1.2.3.4", 1234),
        "server": ("testserver", 80),
    }
    pending = list(chunks)
    reads = 0
    messages = []

    async def receive():
        nonlocal reads
        if not pending:
            return {"type": "http.disconnect"}
        reads += 1
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    return status, reads


def test_declared_content_length_over_cap_is_refused_before_the_handler(upload_app):
    app, saved = upload_app
    status, reads = _call_asgi(app, "/api/upload", [b"x" * CHUNK], content_length=CHAT_CAP + 1)
    assert status == 413
    assert reads == 0, "no byte of the body may be read"
    assert saved == []


def test_chunked_body_over_cap_is_aborted_while_streaming(upload_app):
    app, saved = upload_app
    body = _multipart(b"x" * (CHAT_CAP * 2))
    chunks = [body[i:i + CHUNK] for i in range(0, len(body), CHUNK)]
    status, reads = _call_asgi(app, "/api/upload", chunks)
    assert status == 413
    assert saved == []
    # Aborted at the first chunk past the cap, not after the whole body.
    assert reads == CHAT_CAP // CHUNK + 1
    assert reads < len(chunks)


@pytest.mark.parametrize("streamed", [False, True], ids=["content-length", "chunked"])
def test_small_upload_still_succeeds(upload_app, streamed):
    app, saved = upload_app
    body = _multipart(b"hello")
    content = iter([body[:10], body[10:]]) if streamed else body
    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            content=content,
            headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
        )
    assert response.status_code == 200, response.text
    assert [f["name"] for f in response.json()["files"]] == ["note.txt"]
    assert saved == ["note.txt"]


@pytest.mark.parametrize("path", ["/api/documents/import-pdf", "/api/documents/import-docx"])
def test_document_import_routes_are_capped_at_one_file(path):
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    cap = MAX_FILE_BYTES + MULTIPART_OVERHEAD_BYTES
    assert upload_body_cap(path) == cap
    app = UploadBodyLimitMiddleware(inner)
    assert _call_asgi(app, path, [], content_length=cap + 1) == (413, 0)
    assert calls == []
    assert _call_asgi(app, path, [b""], content_length=cap)[0] == 200
    assert calls == [path]


def test_other_routes_are_not_capped():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = UploadBodyLimitMiddleware(inner)
    assert upload_body_cap("/api/document") is None
    assert _call_asgi(app, "/api/document", [b""], content_length=CHAT_CAP * 10)[0] == 200
    assert calls == ["/api/document"]


def test_app_registers_the_upload_body_cap():
    from app import app

    assert UploadBodyLimitMiddleware in [m.cls for m in app.user_middleware]


def test_real_app_refuses_an_over_cap_upload_before_the_handler(monkeypatch):
    """The app object app.py builds answers an over-cap upload with 413 and
    never reaches UploadHandler.save_upload, so the cap only holds while the
    middleware is registered there."""
    import sys

    from src.upload_handler import UploadHandler

    saved = []

    def save_upload(self, u, client_ip, owner=None):
        saved.append(u.filename)
        return {"id": "0" * 32 + ".txt", "name": u.filename, "mime": "text/plain",
                "size": 5, "hash": "h", "uploaded_at": "now", "width": None,
                "height": None, "is_duplicate": False}

    monkeypatch.setattr(UploadHandler, "save_upload", save_upload)
    # Build a fresh app with auth off (the autouse fixture), its upload routes
    # on a fresh router instead of one an earlier import already filled.
    monkeypatch.setattr(upload_routes, "router", APIRouter(prefix="/api/upload", tags=["upload"]))
    monkeypatch.delitem(sys.modules, "app", raising=False)
    import app as app_mod

    response = TestClient(app_mod.app).post(
        "/api/upload",
        content=_multipart(b"x" * (CHAT_CAP + 1)),
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    assert response.status_code == 413, response.text
    assert saved == []
