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
from src import ai_interaction
import pytest


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
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json, headers):  # pragma: no cover - body irrelevant
        return _GenerationResponse("https://provider.invalid/x.png")


class _FakeResp:
    def __init__(self, status_code=200, content=b"PNGDATA"):
        self.status_code = status_code
        self.content = content


class _RecordingFetch:
    """Stand-in for ``src.outbound_fetch.fetch_public_url``.

    Records every call so tests can assert whether the unsafe URL was even
    attempted.
    """

    instances: list["_RecordingFetch"] = []

    def __init__(self):
        self.calls: list[tuple] = []
        self.raise_on_call: Exception | None = None
        self.return_status = 200
        _RecordingFetch.instances.append(self)

    def __call__(self, url, headers=None, timeout=30, **kwargs):
        self.calls.append((url, headers, timeout))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return _FakeResp(status_code=self.return_status, content=b"PNGDATA")


@pytest.fixture
def patched_fetch(monkeypatch):
    """Stub the heavy bits so ``ai_interaction`` can be imported and exercised.

    Returns the ``_RecordingFetch`` instance so individual tests can inspect
    what ``fetch_public_url`` was called with.
    """
    import httpx
    import src.settings as settings

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    import src.outbound_fetch as outbound_fetch

    fetch = _RecordingFetch()
    monkeypatch.setattr(outbound_fetch, "fetch_public_url", fetch)
    _RecordingFetch.instances = [fetch]

    monkeypatch.setattr(
        ai_interaction,
        "_resolve_model",
        lambda model_spec, owner=None: (
            "https://api.openai.example/v1/chat/completions",
            "dall-e-3",
            {"Authorization": "Bearer test"},
        ),
    )
    return fetch


async def test_generate_image_routes_public_result_through_shared_fetcher(patched_fetch):
    provider_url = "https://images.example.com/generated.png?sig=abc"

    # Patch the provider response to return our URL.
    import httpx

    class _Gen(_AsyncClient):
        def post(self, url, json, headers):
            return _GenerationResponse(provider_url)

    httpx.AsyncClient = _Gen  # type: ignore

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" not in result
    assert patched_fetch.calls, "fetch_public_url must have been called"
    assert patched_fetch.calls[0][0] == provider_url


async def test_generate_image_blocks_link_local_result_without_fetch(patched_fetch):
    unsafe_url = "http://169.254.169.254/latest/meta-data"
    import httpx

    class _Gen(_AsyncClient):
        def post(self, url, json, headers):
            return _GenerationResponse(unsafe_url)

    httpx.AsyncClient = _Gen  # type: ignore

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" in result
    assert "unsafe image URL" in result["error"]
    # The unsafe URL was rejected by the shared fetcher's SSRF guard, so the
    # helper must not have issued the fetch.
    assert all(call[0] != unsafe_url for call in patched_fetch.calls), (
        "fetch_public_url must reject the link-local URL before opening a socket"
    )


async def test_generate_image_blocks_loopback_result_unconditionally(patched_fetch):
    # The previous version only blocked loopback when IMAGE_BLOCK_PRIVATE_IPS
    # was true. After #5888 it is unconditional on this hop.
    loopback_url = "http://127.0.0.1/diffusion/result.png"
    import httpx

    class _Gen(_AsyncClient):
        def post(self, url, json, headers):
            return _GenerationResponse(loopback_url)

    httpx.AsyncClient = _Gen  # type: ignore

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert "error" in result
    assert "unsafe image URL" in result["error"]


async def test_generate_image_non_200_result_falls_back_to_url(patched_fetch):
    provider_url = "https://images.example.com/generated.png?sig=abc"
    patched_fetch.return_status = 503
    import httpx

    class _Gen(_AsyncClient):
        def post(self, url, json, headers):
            return _GenerationResponse(provider_url)

    httpx.AsyncClient = _Gen  # type: ignore

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    # The upstream URL is reachable (we only stubbed fetch_public_url to
    # return 503), so the helper falls back to the external URL instead of
    # erroring out — the user still gets something.
    assert result.get("image_url") == provider_url