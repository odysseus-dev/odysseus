"""The gallery image-edit proxies (inpaint, harmonize) accept an upstream
diffusion / OpenAI response that may carry an image *URL* instead of inline
base64, and then fetch that URL server-side. That URL is controlled by whatever
server the request was sent to, so a malicious or compromised endpoint can
return e.g. ``http://169.254.169.254/...`` and turn the result fetch into an
SSRF primitive (cloud-metadata credential exfil).

The client-supplied ``_endpoint`` is already validated through
``check_outbound_url`` before the first request; this pins the same guard on the
*result* URL pulled from the response body, which previously went unchecked.
"""
import base64

import pytest
from fastapi import HTTPException

import routes.gallery_routes as gallery_routes


class _FakeResp:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _FakeAsyncClient:
    instances: list["_FakeAsyncClient"] = []

    def __init__(self, *args, **kwargs):
        self.gets: list[str] = []
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self.gets.append(url)
        return _FakeResp(200, b"PNGDATA")


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    import httpx

    _FakeAsyncClient.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


async def test_rejects_link_local_result_url():
    # A compromised upstream returns the cloud-metadata address as the image
    # URL. The helper must refuse it and never issue the fetch.
    with pytest.raises(HTTPException) as exc:
        await gallery_routes._fetch_result_image_b64(
            "http://169.254.169.254/latest/meta-data"
        )
    assert exc.value.status_code == 502
    assert all(c.gets == [] for c in _FakeAsyncClient.instances), (
        "the unsafe result URL must not be fetched"
    )


async def test_fetches_safe_result_url():
    # A normal loopback/LAN diffusion server result URL is allowed (local-first)
    # and returned base64-encoded, matching the prior inline behavior.
    out = await gallery_routes._fetch_result_image_b64("http://127.0.0.1/img.png")
    assert out == base64.b64encode(b"PNGDATA").decode()
