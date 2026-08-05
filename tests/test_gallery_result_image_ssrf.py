"""The gallery image-edit proxies (inpaint, harmonize) accept an upstream
diffusion / OpenAI response that may carry an image *URL* instead of inline
base64, and then fetch that URL server-side. That URL is controlled by whatever
server the request was sent to, so a malicious or compromised endpoint can
return e.g. ``http://169.254.169.254/...`` and turn the result fetch into an
SSRF primitive (cloud-metadata credential exfil).

The client-supplied ``_endpoint`` is already validated through
``check_outbound_url`` before the first request; this pins the same guard on the
*result* URL pulled from the response body via the shared
``src.outbound_fetch.fetch_public_url``, which resolves once, validates as
public, pins the TCP connect to that resolved IP, and refuses the fetch on
private addresses unconditionally — there is no env-flag opt-out for this hop
because the operator-configured-endpoint rationale in ``src/url_safety.py`` does
not extend to an arbitrary URL a remote server hands back (#5888).
"""
import base64

import pytest
from fastapi import HTTPException

import routes.gallery_routes as gallery_routes


class _FakeResp:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _FakeFetch:
    """Stand-in for ``src.outbound_fetch.fetch_public_url``.

    Records every call so tests can assert whether the helper tried to fetch
    an unsafe URL.
    """

    instances: list["_FakeFetch"] = []

    def __init__(self, *args, **kwargs):
        self.calls: list[tuple] = []
        _FakeFetch.instances.append(self)

    def __call__(self, url, headers=None, timeout=30, **kwargs):
        self.calls.append((url, headers, timeout))
        return _FakeResp(200, b"PNGDATA")


@pytest.fixture(autouse=True)
def _fake_fetch(monkeypatch):
    _FakeFetch.instances = []
    import src.outbound_fetch as outbound_fetch

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _FakeFetch())
    yield


async def test_rejects_link_local_result_url():
    # A compromised upstream returns the cloud-metadata address as the image
    # URL. The helper must refuse it and never issue the fetch.
    with pytest.raises(HTTPException) as exc:
        await gallery_routes._fetch_result_image_b64(
            "http://169.254.169.254/latest/meta-data"
        )
    assert exc.value.status_code == 502
    assert all(
        not c.calls for c in _FakeFetch.instances
    ), "the unsafe result URL must not be fetched"


async def test_rejects_loopback_result_url():
    # The previous version allowed ``http://127.0.0.1/...`` here because the
    # operator could opt into ``IMAGE_BLOCK_PRIVATE_IPS=true``. After #5888
    # the second hop refuses private addresses unconditionally — a local
    # diffusion server returns b64_json directly and never the URL branch, so
    # this tightening costs no supported local setup.
    with pytest.raises(HTTPException) as exc:
        await gallery_routes._fetch_result_image_b64("http://127.0.0.1/img.png")
    assert exc.value.status_code == 502


async def test_rejects_metadata_hostname_result_url():
    # The Google cloud-metadata hostname is rejected by name even before the
    # resolver runs.
    with pytest.raises(HTTPException) as exc:
        await gallery_routes._fetch_result_image_b64(
            "http://metadata.google.internal/computeMetadata/v1/"
        )
    assert exc.value.status_code == 502


async def test_fetches_public_result_url_and_returns_base64():
    # A public IP is allowed through, the response body is base64-encoded and
    # returned, and the helper used the shared fetcher (single source of
    # truth for SSRF pinning).
    public_url = "http://93.184.216.34/img.png"
    out = await gallery_routes._fetch_result_image_b64(public_url)
    assert out == base64.b64encode(b"PNGDATA").decode()
    assert _FakeFetch.instances, "fetch_public_url must have been called"
    # Exactly one call, with our public URL.
    assert _FakeFetch.instances[0].calls[0][0] == public_url


async def test_non_200_response_yields_none():
    # When the upstream returns a non-200, the gallery shows the external URL
    # as a fallback, so the helper must return None without raising.
    import src.outbound_fetch as outbound_fetch

    class _OtherFetch(_FakeFetch):
        def __init__(self):
            super().__init__()
            self.calls = []

        def __call__(self, url, headers=None, timeout=30, **kwargs):
            self.calls.append((url, headers, timeout))
            return _FakeResp(404, b"")

    outbound_fetch.fetch_public_url = _OtherFetch()
    out = await gallery_routes._fetch_result_image_b64("http://93.184.216.34/missing.png")
    assert out is None