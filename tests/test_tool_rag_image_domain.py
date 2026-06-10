"""Regression: the agent tool-RAG domain classifier had no image/media domain,
so image-generation requests matched no domain, were flagged low_signal, and had
tool retrieval SKIPPED entirely — the model only received ALWAYS_AVAILABLE tools
(manage_memory, ask_user, update_plan) and never `generate_image`/`edit_image`,
so it could not generate images (and tended to loop on manage_memory).

Root cause: `_classify_agent_request` in src/agent_loop.py sets
`low_signal = not continuation and not domains`; with no `images` domain, prompts
like "generate two images of X" matched nothing -> low_signal -> retrieval skipped.

The classifier is deterministic string matching (no embeddings / no DB), so it
can be exercised directly.
"""

from src.agent_loop import (
    _classify_agent_request,
    _DOMAIN_TOOL_MAP,
    _DOMAIN_RULES,
    _domain_rules_for_tools,
)


def _classify(text):
    return _classify_agent_request([{"role": "user", "content": text}], text)


def test_image_generation_requests_get_image_domain():
    """Image-generation phrasings must match the `images` domain and NOT be
    treated as low-signal (which would skip tool retrieval)."""
    prompts = [
        "generate two images of this character: one action pose, one relaxed",
        "draw me a picture of a cat",
        "make an illustration of a spaceship",
        "create an image of a sunset over mountains",
        "design a logo for my coffee shop",
    ]
    for p in prompts:
        intent = _classify(p)
        assert "images" in intent["domains"], f"expected images domain for: {p!r}"
        assert intent["low_signal"] is False, f"must not be low_signal: {p!r}"


def test_image_edit_requests_get_image_domain():
    """Edit/upscale/background phrasings also resolve to the image domain."""
    for p in ("upscale image 5", "remove the background from this photo", "inpaint the selected area"):
        intent = _classify(p)
        assert "images" in intent["domains"], f"expected images domain for: {p!r}"


def test_image_domain_seeds_generate_and_edit_image():
    """The domain must seed the actual image tools so they are offered even when
    semantic retrieval misses."""
    assert _DOMAIN_TOOL_MAP["images"] == {"generate_image", "edit_image"}


def test_image_domain_has_a_rule_pack():
    """Every domain in _DOMAIN_TOOL_MAP needs a matching _DOMAIN_RULES entry,
    otherwise _domain_rules_for_tools raises KeyError when the tools are selected."""
    assert "images" in _DOMAIN_RULES
    rules = _domain_rules_for_tools({"generate_image"})
    assert any("Image rules" in r for r in rules)


def test_non_image_requests_do_not_match_image_domain():
    """Guard against over-triggering: ordinary prompts must not be flagged image."""
    assert "images" not in _classify("what is the capital of France")["domains"]
    assert "images" not in _classify("reply to the latest email in my inbox")["domains"]
