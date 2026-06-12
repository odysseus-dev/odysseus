"""Regression for issue #1707 — the agent tool-RAG force-included the entire
email toolset on any "tell me ..." query, crowding out the relevant tools so the
model believed it only had email tools and refused web/other tasks.

Root cause: `_KEYWORD_HINTS` in src/tool_index.py listed "tell" under the email
intent, and `get_tools_for_query` force-includes a hint's tools whenever any of
its keywords appears (word-boundary match). "tell" appears in a huge fraction of
requests (the reporter's was "visit <url> and tell me the title"), so email tools
were force-included for non-email queries.

These hints are deterministic string matching — no embeddings — so we can test
`get_tools_for_query` directly with retrieval stubbed out (no ChromaDB needed).
"""

from src.tool_index import (
    ToolIndex,
    ALWAYS_AVAILABLE,
    IMAGE_CAPABILITY_FORBIDDEN_TOOLS,
    apply_image_capability_tool_selection,
    is_concrete_image_creation_prompt,
    is_image_capability_question,
    should_preroute_image_discovery,
)

_EMAIL_TOOLS = {
    "list_emails", "read_email", "send_email", "reply_to_email",
    "bulk_email", "delete_email", "archive_email", "mark_email_read",
}


def _index_without_embeddings():
    """A ToolIndex whose retrieval returns nothing, so get_tools_for_query
    exercises only the deterministic base + keyword-hint logic."""
    ti = ToolIndex.__new__(ToolIndex)        # skip __init__ (no ChromaDB/fastembed)
    ti.retrieve = lambda query, k=8: []
    return ti


def test_tell_in_web_query_does_not_force_email_tools():
    """The #1707 repro: a web request that merely contains the word 'tell' must
    NOT drag in the email toolset."""
    ti = _index_without_embeddings()
    q = "visit https://www.youtube.com/user/PewDiePie and tell me the title of his latest video"
    tools = ti.get_tools_for_query(q)
    leaked = _EMAIL_TOOLS & tools
    assert not leaked, f"'tell me' must not force-include email tools, got {sorted(leaked)}"
    # web_search / web_fetch are always-available and must remain present.
    assert "web_search" in tools and "web_fetch" in tools


def test_explicit_web_search_query_gets_web_tools_without_retrieval():
    """Explicit web-search phrasing must surface web tools even if embeddings
    return nothing."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("use web search and find a recipe for chocolate chip cookies")
    assert "web_search" in tools and "web_fetch" in tools


def test_genuine_email_query_still_gets_email_tools():
    """Removing 'tell' must not break real email intent — the actual email
    keywords still force-include the toolset."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("reply to the unread email in my inbox")
    assert {"reply_to_email", "send_email", "read_email"} <= tools


def test_plain_tell_request_stays_minimal():
    """A bare 'tell me a joke' must not pull in email tools either."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("tell me a joke")
    assert not (_EMAIL_TOOLS & tools)
    # Always-available baseline is still there.
    assert set(ALWAYS_AVAILABLE) <= tools


# Image capability discovery — must surface list_media_models, never a
# hallucinated "draw" tool, and must not force generate_image on questions.

_CAPABILITY_QUERIES = (
    "Can you make images?",
    "Can you draw?",
    "Do you support image generation?",
    "Can you generate pictures?",
    "What image models are available?",
)


def test_image_capability_queries_surface_list_media_models_only():
    ti = _index_without_embeddings()
    for q in _CAPABILITY_QUERIES:
        tools = ti.get_tools_for_query(q)
        assert "list_media_models" in tools, q
        leaked = IMAGE_CAPABILITY_FORBIDDEN_TOOLS & tools
        assert not leaked, f"{q} must not surface {sorted(leaked)}"


def test_is_image_capability_question_detects_common_prompts():
    for q in _CAPABILITY_QUERIES:
        assert is_image_capability_question(q), q
    assert not is_image_capability_question("draw a cat in watercolor")
    assert not is_image_capability_question("tell me a joke")


def test_image_capability_filter_strips_rag_retrieved_image_tools():
    ti = ToolIndex.__new__(ToolIndex)
    ti.retrieve = lambda query, k=8: [
        "edit_image", "generate_image", "draw", "image_editing",
    ]
    tools = ti.get_tools_for_query("Can you make images?")
    assert "list_media_models" in tools
    leaked = IMAGE_CAPABILITY_FORBIDDEN_TOOLS & tools
    assert not leaked


def test_apply_image_capability_tool_selection_is_noop_for_creation():
    base = {"list_media_models", "generate_image", "bash"}
    assert apply_image_capability_tool_selection(base, "draw a red fox") == base


def test_image_creation_intent_still_surfaces_generation_tools():
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("draw a cat in watercolor style")
    assert "list_media_models" in tools
    assert "generate_image" in tools
    assert "draw" not in tools


_CREATION_PROMPT = "Generate an image of a red bicycle on a white background."


def test_concrete_creation_prompt_is_detected():
    assert is_concrete_image_creation_prompt(_CREATION_PROMPT)
    assert not is_concrete_image_creation_prompt("Can you make images?")


def test_capability_prompt_preroutes_with_no_config():
    assert should_preroute_image_discovery(
        "Can you make images?",
        settings={"media_models": [], "default_image_media_model": "", "image_model": ""},
    ) == "capability"


def test_concrete_creation_preroutes_with_no_config():
    settings = {"media_models": [], "default_image_media_model": "", "image_model": ""}
    assert should_preroute_image_discovery(_CREATION_PROMPT, settings=settings) == "creation"


def test_concrete_creation_preroutes_generate_image_when_media_model_configured():
    settings = {
        "media_models": [{
            "id": "qwen-image",
            "provider": "comfyui",
            "kind": "image",
            "enabled": True,
            "isDefault": True,
        }],
        "default_image_media_model": "qwen-image",
        "image_model": "",
    }
    assert should_preroute_image_discovery(_CREATION_PROMPT, settings=settings) == "configured_creation"


def test_concrete_creation_surfaces_generation_route_when_configured():
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query(_CREATION_PROMPT)
    assert "list_media_models" in tools
    assert "generate_image" in tools
    for invented in ("draw", "image_editing", "sstablediff"):
        assert invented not in tools


def test_concrete_creation_unconfigured_hints_exclude_invented_tools():
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query(_CREATION_PROMPT)
    assert "list_media_models" in tools
    assert "generate_image" in tools
    for invented in ("draw", "image_editing", "sstablediff"):
        assert invented not in tools
