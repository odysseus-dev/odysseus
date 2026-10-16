"""Tests for the seneca single-image op proxies (HARNESS-owned).

POST /api/image/replace-background -> provider /v1/images/replace-background
POST /api/image/outpaint           -> provider /v1/images/outpaint (alias /extend)

Both forward a client-supplied JPEG/PNG as multipart with the prompt/params plus
``request_id`` (so GET /api/image/edit-progress lights the progress bar) and
return the provider image as ``{image}``.
"""
import asyncio
import base64
from types import SimpleNamespace

import httpx

import routes.gallery_routes as gallery

_IMG_B64 = base64.b64encode(b"abc-not-a-real-png").decode()


def _route(path):
    return next(r for r in gallery.setup_gallery_routes().routes if r.path == path)


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _fake_db():
    class Db:
        def close(self):
            pass

        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    return Db()


def _setup(monkeypatch, respond):
    endpoint = SimpleNamespace(base_url="http://100.69.120.65:8188/v1", api_key="k")
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(gallery, "_first_visible_image_endpoint", lambda *a: endpoint)
    seen = {}

    def _respond(request):
        seen["url"] = str(request.url)
        seen["body"] = bytes(request.content)
        return respond(request)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **k: client(transport=httpx.MockTransport(_respond), **k),
    )
    return seen


def _ok(_req):
    return httpx.Response(200, json={"data": [{"b64_json": "AAAA"}]})


def test_replace_background_forwards_multipart_and_returns_image(monkeypatch):
    seen = _setup(monkeypatch, _ok)
    out = asyncio.run(_route("/api/image/replace-background").endpoint(_Req({
        "image": _IMG_B64, "prompt": "put her on a beach", "steps": 8,
        "harmonise": True, "shadow": False, "request_id": "rb-123",
    })))
    assert out["image"] == "AAAA"
    assert seen["url"].endswith("/images/replace-background")
    body = seen["body"]
    assert b"put her on a beach" in body
    assert b"rb-123" in body            # request_id forwarded for progress
    assert b"harmonise" in body and b"shadow" in body
    assert b"8" in body                  # steps


def test_outpaint_forwards_ratio_size_context_and_returns_image(monkeypatch):
    seen = _setup(monkeypatch, _ok)
    out = asyncio.run(_route("/api/image/outpaint").endpoint(_Req({
        "image": _IMG_B64, "prompt": "extend the sky", "ratio": "16:9",
        "size": "1920x1080", "steps": 4, "context": "calm blue sky",
        "request_id": "op-9",
    })))
    assert out["image"] == "AAAA"
    assert seen["url"].endswith("/images/outpaint")
    body = seen["body"]
    for token in (b"16:9", b"1920x1080", b"calm blue sky", b"op-9"):
        assert token in body, token


def test_outpaint_extend_alias_uses_the_same_provider_route(monkeypatch):
    seen = _setup(monkeypatch, _ok)
    out = asyncio.run(_route("/api/image/extend").endpoint(_Req({
        "image": _IMG_B64, "prompt": "extend", "ratio": "9:16",
    })))
    assert out["image"] == "AAAA"
    assert seen["url"].endswith("/images/outpaint")


def test_outpaint_reads_openai_style_url_result(monkeypatch):
    def _respond(_req):
        return httpx.Response(200, json={"data": [{"url": "http://100.69.120.65:8188/v1/images/x.png"}]})

    _setup(monkeypatch, _respond)
    monkeypatch.setattr(gallery, "_fetch_result_image_b64", lambda url: _async_return("ZZZZ"))
    out = asyncio.run(_route("/api/image/outpaint").endpoint(_Req({"image": _IMG_B64, "prompt": "x"})))
    assert out["image"] == "ZZZZ"


def test_replace_background_502_when_provider_returns_no_image(monkeypatch):
    _setup(monkeypatch, lambda _req: httpx.Response(200, json={"data": []}))
    try:
        asyncio.run(_route("/api/image/replace-background").endpoint(_Req({"image": _IMG_B64})))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 502
    else:
        raise AssertionError("expected a 502 when the provider returns no image")


async def _async_return(value):
    return value


def _capture_forward(monkeypatch):
    """Replace _forward_seneca_image_op with a fake that records kwargs."""
    captured = {}

    async def _fake_forward(base, api_key, provider_path, image_bytes, fields, *, label, timeout=300.0):
        captured["label"] = label
        captured["provider_path"] = provider_path
        captured["timeout"] = timeout
        return {"image": "AAAA"}

    monkeypatch.setattr(gallery, "_forward_seneca_image_op", _fake_forward)
    monkeypatch.setattr(gallery, "SessionLocal", lambda: _fake_db())
    monkeypatch.setattr(gallery, "require_privilege", lambda *a: "alice")
    monkeypatch.setattr(
        gallery, "_first_visible_image_endpoint",
        lambda *a: SimpleNamespace(base_url="http://100.69.120.65:8188/v1", api_key="k"),
    )
    return captured


def test_outpaint_proxy_uses_a_long_timeout(monkeypatch):
    captured = _capture_forward(monkeypatch)
    out = asyncio.run(_route("/api/image/outpaint").endpoint(_Req({"image": _IMG_B64, "prompt": "extend to 9:16"})))
    assert out["image"] == "AAAA"
    assert captured["provider_path"] == "/images/outpaint"
    assert captured["timeout"] >= 900, captured


def test_outpaint_extend_alias_also_uses_the_long_timeout(monkeypatch):
    captured = _capture_forward(monkeypatch)
    asyncio.run(_route("/api/image/extend").endpoint(_Req({"image": _IMG_B64, "prompt": "extend"})))
    assert captured["timeout"] >= 900, captured


def test_replace_background_keeps_the_short_default_timeout(monkeypatch):
    captured = _capture_forward(monkeypatch)
    asyncio.run(_route("/api/image/replace-background").endpoint(_Req({"image": _IMG_B64, "prompt": "beach"})))
    assert captured["timeout"] == 300.0, captured


def test_outpaint_timeout_env_override(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OUTPAINT_TIMEOUT", "720")
    assert gallery._outpaint_timeout() == 720.0
    monkeypatch.delenv("ODYSSEUS_IMAGE_OUTPAINT_TIMEOUT", raising=False)
    assert gallery._outpaint_timeout() == 900.0


def test_graceful_shutdown_window_covers_a_long_outpaint():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    assert '"ODYSSEUS_GRACEFUL_SHUTDOWN_TIMEOUT", "900"' in src
