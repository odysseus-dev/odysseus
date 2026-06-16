from src import ai_interaction


class _GenerationResponse:
    status_code = 200
    text = ""

    def __init__(self, image_url):
        self._image_url = image_url

    def json(self):
        return {"data": [{"url": self._image_url}]}


class _DownloadResponse:
    status_code = 503
    content = b""


def _patch_generation(monkeypatch, image_url):
    async def _post(self, url, json, headers):
        return _GenerationResponse(image_url)

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        post = _post

    import httpx
    import src.settings as settings

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        ai_interaction,
        "_resolve_model",
        lambda model_spec, owner=None: (
            "https://api.openai.example/v1/chat/completions",
            "dall-e-3",
            {"Authorization": "Bearer test"},
        ),
    )


async def test_generate_image_validates_provider_url_before_download(monkeypatch):
    import httpx
    import src.url_safety as url_safety

    provider_url = "https://images.example.com/generated.png?sig=abc"
    events = []
    _patch_generation(monkeypatch, provider_url)

    def _check_outbound_url(url, *, block_private=False):
        events.append(("check", url, block_private))
        return True, "ok"

    def _get(url, *, timeout):
        events.append(("get", url, timeout))
        return _DownloadResponse()

    monkeypatch.setattr(url_safety, "check_outbound_url", _check_outbound_url)
    monkeypatch.setattr(httpx, "get", _get)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert result["image_url"] == provider_url
    assert events == [
        ("check", provider_url, False),
        ("get", provider_url, 60),
    ]


async def test_generate_image_rejects_unsafe_provider_url_without_download(monkeypatch):
    import httpx
    import src.url_safety as url_safety

    unsafe_url = "http://169.254.169.254/latest/meta-data"
    events = []
    _patch_generation(monkeypatch, unsafe_url)

    def _check_outbound_url(url, *, block_private=False):
        events.append(("check", url, block_private))
        return False, "link-local address blocked (SSRF metadata risk): 169.254.169.254"

    def _get(url, *, timeout):
        raise AssertionError("unsafe provider image URL must not be downloaded")

    monkeypatch.setattr(url_safety, "check_outbound_url", _check_outbound_url)
    monkeypatch.setattr(httpx, "get", _get)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert result["error"] == (
        "Image API returned unsafe image URL: "
        "link-local address blocked (SSRF metadata risk): 169.254.169.254"
    )
    assert events == [("check", unsafe_url, False)]
