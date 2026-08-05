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

import httpx
import pytest
from fastapi import HTTPException

import routes.gallery.gallery_routes as gallery_routes


class _FakeResp:
    def __init__(self, status_code: int = 200, content: bytes = b"PNGDATA"):
        self.status_code = status_code
        self.content = content


def _is_unsafe_url(url: str) -> bool:
    """Mirror ``src.outbound_fetch._resolve_public_ips`` for the test fixtures
    — private / loopback / link-local / metadata hostnames are rejected
    *before* any socket is opened."""
    lowered = url.lower()
    if "metadata.google.internal" in lowered or "169.254." in lowered or "127.0.0.1" in lowered:
        return True
    return False


class _RecordingFetch:
    """Stand-in for ``src.outbound_fetch.fetch_public_url``.

    Records every call and mirrors the real helper's SSRF guard: private /
    loopback / link-local / metadata hostnames raise ``httpx.RequestError``
    *before* any socket is opened, and any other URL returns a 200 fake
    response so tests can assert the safe path went through.
    """

    _call_log: list = []

    def __call__(self, url, headers=None, timeout=30, **kwargs):
        _RecordingFetch._call_log.append((url, headers, timeout))
        if _is_unsafe_url(url):
            raise httpx.RequestError(f"Blocked non-public URL: {url}")
        return _FakeResp()


@pytest.fixture(autouse=True)
def _fake_fetch(monkeypatch):
    _RecordingFetch._call_log = []
    import src.outbound_fetch as outbound_fetch

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _RecordingFetch())
    yield _RecordingFetch


async def test_rejects_link_local_result_url():
    # A compromised upstream returns the cloud-metadata address as the image
    # URL. The shared fetcher must refuse it before opening a socket, and the
    # gallery helper must surface that as a 502 to the caller.
    with pytest.raises(HTTPException) as exc:
        await gallery_routes._fetch_result_image_b64(
            "http://169.254.169.254/latest/meta-data"
        )
    assert exc.value.status_code == 502
    assert _RecordingFetch._call_log == [], (
        "the unsafe result URL must not reach fetch_public_url"
    )


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
    assert len(_RecordingFetch._call_log) == 1
    assert _RecordingFetch._call_log[0][0] == public_url


async def test_non_200_response_yields_none(monkeypatch):
    # When the upstream returns a non-200, the gallery shows the external URL
    # as a fallback, so the helper must return None without raising.
    import src.outbound_fetch as outbound_fetch

    def _non200(url, headers=None, timeout=30, **kwargs):
        return _FakeResp(status_code=404, content=b"")

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _non200)
    out = await gallery_routes._fetch_result_image_b64("http://93.184.216.34/missing.png")
    assert out is None