"""Server health audit fix: the local editor VLM returned 400s when asked for a
model id it does not serve (the client default 'Qwen3.5-9B-MLX-8bit', or the
stale caption model 'Qwen/Qwen3-VL-30B'). ``_resolve_model`` maps the requested
id to one the base actually serves."""
import asyncio

from src import editor_agent_model as eam


def _cache(base, ids):
    eam._MODELS_CACHE[base] = set(ids)


def test_resolve_model_keeps_a_served_request():
    base = "http://x:1/v1"
    _cache(base, ["Qwen/Qwen3.5-9B", "other"])
    assert asyncio.run(eam._resolve_model(base, "Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-9B")) == "Qwen/Qwen3.5-9B"


def test_resolve_model_falls_back_when_requested_is_not_served():
    base = "http://x:2/v1"
    _cache(base, ["Qwen/Qwen3.5-9B"])
    # e.g. the client's MLX build name, or a stale caption model id.
    assert asyncio.run(eam._resolve_model(base, "Qwen3.5-9B-MLX-8bit", "Qwen/Qwen3.5-9B")) == "Qwen/Qwen3.5-9B"
    assert asyncio.run(eam._resolve_model(base, "Qwen/Qwen3-VL-30B", "Qwen/Qwen3.5-9B")) == "Qwen/Qwen3.5-9B"


def test_resolve_model_picks_a_served_id_when_fallback_absent():
    base = "http://x:3/v1"
    _cache(base, ["B", "A"])
    assert asyncio.run(eam._resolve_model(base, "nope", "also-nope")) == "A"


def test_resolve_model_keeps_request_when_served_list_unknown():
    base = "http://x:4/v1"
    _cache(base, [])
    # Server unreachable / no models listed -> don't rewrite the request.
    assert asyncio.run(eam._resolve_model(base, "some/model", "fallback")) == "some/model"


def test_self_check_resolves_the_model_before_calling(monkeypatch):
    monkeypatch.setattr(eam, "local_bases", lambda: ["http://editor.test/v1"])
    base = eam.local_bases()[0]
    _cache(base, ["Qwen/Qwen3.5-9B"])
    seen = {}

    async def fake_post(b, model, messages, headers, timeout, **kwargs):
        seen["model"] = model
        return "DONE"

    monkeypatch.setattr(eam, "_post_chat", fake_post)
    out = asyncio.run(eam.self_check(
        "data:image/png;base64,AAAA", "data:image/png;base64,BBBB",
        "make it brighter", model="Qwen3.5-9B-MLX-8bit"))
    assert seen["model"] == "Qwen/Qwen3.5-9B"
    assert out["done"] is True


def test_caption_resolves_the_caption_model(monkeypatch):
    monkeypatch.setattr(eam, "caption_base", lambda: "http://caption.test/v1")
    seen = {}

    async def fake_post(b, model, messages, headers, timeout, **kwargs):
        seen["base"] = b
        seen["model"] = model
        return "a photo"

    monkeypatch.setattr(eam, "_post_chat", fake_post)
    import src.editor_agent_model as m
    _cache(m.caption_base(), ["Qwen/Qwen3.5-9B"])
    txt = asyncio.run(m.caption_image("data:image/png;base64,AAAA"))
    assert txt == "a photo"
    assert seen["model"] == "Qwen/Qwen3.5-9B"


def test_unconfigured_editor_helpers_do_not_probe_private_hosts(monkeypatch):
    monkeypatch.setattr(eam, "local_bases", lambda: [])
    monkeypatch.setattr(eam, "caption_base", lambda: "")
    monkeypatch.setattr(eam, "probe_models", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected probe")))
    eam._probe_worker()
    assert asyncio.run(eam.caption_image("data:image/png;base64,AAAA")) is None
    result = asyncio.run(eam.self_check("data:image/png;base64,AAAA", "data:image/png;base64,BBBB", "brighter"))
    assert result["reason"] == "unavailable"


def test_unconfigured_or_untrusted_override_never_becomes_endpoint(monkeypatch):
    monkeypatch.setattr(eam, "local_bases", lambda: [])
    import src.endpoint_resolver as resolver
    monkeypatch.setattr(resolver, "resolve_endpoint", lambda *a, **k: (None, None, None))
    assert eam._candidates("alice", "chosen-model") == []
    assert eam._candidates("alice", "http://169.254.169.254::model") == []
