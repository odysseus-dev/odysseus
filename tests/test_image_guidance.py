"""Image guidance scaffold — default OFF must not mutate tool selection."""

from titan.image_guidance import (
    apply_image_tool_guidance,
    guidance_enabled,
    load_image_guidance_config,
)


def test_guidance_disabled_by_default():
    cfg = load_image_guidance_config(force=True)
    assert cfg.pin_generate_image_rag is False
    assert cfg.auto_resume_pending_workflow is False
    assert cfg.auto_regenerate_bypass_llm is False
    assert guidance_enabled() is False


def test_apply_guidance_noop_when_off():
    tools = {"ask_user", "web_search"}
    before = set(tools)
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "NEEDS_USER_INPUT: confirm style"},
    ]
    apply_image_tool_guidance(tools, messages, user_text="go")
    assert tools == before
