"""Tests for ``src.ai_interaction.do_generate_image`` and ``do_edit_image``
result-URL safety.

The image-generation and image-edit paths download the result file by URL
when the upstream returns ``{"url": "..."}`` instead of ``{"b64_json": "..."}``.
That URL came back in the upstream response body, so a malicious or
compromised provider can return e.g. ``http://169.254.169.254/...`` and turn
the result fetch into an SSRF primitive. The helper used to be a one-shot
``check_outbound_url`` + ``httpx.get`` pair, which had two holes:

1. DNS rebinding TOCTOU between the guard and the connect.
2. ``IMAGE_BLOCK_PRIVATE_IPS`` defaulted to ``false``, so loopback / RFC1918
   were reachable at the shipped default.

Both sites now route through the shared ``src.outbound_fetch.fetch_public_url``,
which closes both holes by resolving once, pinning the TCP connect, and
unconditionally refusing private addresses (#5888). These tests pin the new
behaviour.
"""
import pytest
import httpx

from src import ai_interaction


class _GenerationResponse:
    status_code = 200
    text = ""

    def __init__(self, image_url):
        self._image_url = image_url

    def json(self):
        return {"data": [{"url": self._image_url}]}


class _AsyncClient:
    """Mock used for the *provider* (first hop) request — the result-URL
    fetch is now offloaded to ``fetch_public_url`` and must be mocked
    separately below."""

    def __init__(self, *args, **kwargs):
        self._image_url = "https://provider.invalid/x.png"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):  # pragma: no cover - body irrelevant
        return _GenerationResponse(self._image_url)


class _FakeResp:
    def __init__(self, status_code=200, content=b"PNGDATA"):
        self.status_code = status_code
        self.content = content


class _RecordingFetch:
    """Stand-in for ``src.outbound_fetch.fetch_public_url``.

    Each call appends to ``_call_log`` (class-level) so tests can assert
    what URL the helper attempted.
    """

    _call_log: list = []

    def __init__(self, status_code=200, content=b"PNGDATA"):
        self._status_code = status_code
        self._content = content

    def __call__(self, url, headers=None, timeout=30, **kwargs):
        _RecordingFetch._call_log.append((url, headers, timeout))
        return _FakeResp(status_code=self._status_code, content=self._content)


@pytest.fixture
def patched_fetch(monkeypatch):
    """Stub the heavy bits so ``ai_interaction`` can be imported and exercised.

    Returns the ``_RecordingFetch`` factory so individual tests can swap in
    a different ``status_code`` if they want.
    """
    import httpx
    import src.settings as settings

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    _RecordingFetch._call_log = []
    import src.outbound_fetch as outbound_fetch

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _RecordingFetch())

    monkeypatch.setattr(
        ai_interaction,
        "_resolve_model",
        lambda model_spec, owner=None: (
            "https://api.openai.example/v1/chat/completions",
            "dall-e-3",
            {"Authorization": "Bearer test"},
        ),
    )
    return _RecordingFetch


def _provider_returns(monkeypatch, image_url):
    """Make ``httpx.AsyncClient``'s ``post`` return a response whose JSON
    carries ``image_url`` as the result URL."""
    import httpx

    class _Gen(_AsyncClient):
        def __init__(self, *args, **kwargs):
            self._image_url = image_url

        async def post(self, url, json, headers):
            return _GenerationResponse(self._image_url)

    monkeypatch.setattr(httpx, "AsyncClient", _Gen)


async def test_generate_image_routes_public_result_through_shared_fetcher(monkeypatch, patched_fetch):
    provider_url = "https://images.example.com/generated.png?sig=abc"
    import httpx
    import src.outbound_fetch as outbound_fetch

    def _factory(url, headers=None, timeout=30, **kwargs):
        _RecordingFetch._call_log.append((url, headers, timeout))
        return _FakeResp(status_code=200, content=b"PNGDATA")

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _factory)
    _provider_returns(monkeypatch, provider_url)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" not in result, result
    assert _RecordingFetch._call_log, "fetch_public_url must have been called"
    assert _RecordingFetch._call_log[0][0] == provider_url


async def test_generate_image_blocks_link_local_result_without_fetch(monkeypatch, patched_fetch):
    unsafe_url = "http://169.254.169.254/latest/meta-data"
    import src.outbound_fetch as outbound_fetch

    def _raise_unsafe(url, headers=None, timeout=30, **kwargs):
        # Mirror the SSRF guard's behaviour: private IPs are rejected
        # *before* any socket is opened, raising ``httpx.RequestError``.
        raise httpx.RequestError(f"Blocked non-public URL: {url}")

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _raise_unsafe)
    _provider_returns(monkeypatch, unsafe_url)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" in result
    assert "unsafe image URL" in result["error"]


async def test_generate_image_blocks_loopback_result_unconditionally(monkeypatch, patched_fetch):
    # The previous version only blocked loopback when IMAGE_BLOCK_PRIVATE_IPS
    # was true. After #5888 it is unconditional on this hop.
    loopback_url = "http://127.0.0.1/diffusion/result.png"
    import httpx
    import src.outbound_fetch as outbound_fetch

    def _raise_loopback(url, headers=None, timeout=30, **kwargs):
        raise httpx.RequestError(f"Blocked non-public URL: {url}")

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _raise_loopback)
    _provider_returns(monkeypatch, loopback_url)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" in result
    assert "unsafe image URL" in result["error"]


async def test_generate_image_non_200_result_falls_back_to_url(monkeypatch, patched_fetch):
    provider_url = "https://images.example.com/generated.png?sig=abc"
    import src.outbound_fetch as outbound_fetch

    def _return_503(url, headers=None, timeout=30, **kwargs):
        return _FakeResp(status_code=503, content=b"")

    monkeypatch.setattr(outbound_fetch, "fetch_public_url", _return_503)
    _provider_returns(monkeypatch, provider_url)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    # The upstream URL is reachable (we only stubbed fetch_public_url to
    # return 503), so the helper falls back to the external URL instead of
    # erroring out — the user still gets something.
    assert result.get("image_url") == provider_url