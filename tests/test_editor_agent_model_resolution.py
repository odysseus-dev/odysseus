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
